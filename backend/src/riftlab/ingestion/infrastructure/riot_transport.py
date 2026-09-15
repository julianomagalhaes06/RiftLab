from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

import httpx

from riftlab.shared.errors import (
    SourceAuthError,
    SourceError,
    SourceNotFoundError,
    SourceRateLimitedError,
    SourceSchemaError,
)

# Convenção amplamente documentada em SDKs, exemplos e Swagger de referência da
# Riot API para chaves development/production (distinta do fluxo RSO/OAuth, que
# usa "Authorization: Bearer"). Reconfirmada nesta sessão via busca dirigida a
# exemplos reais (curl/Python) de terceiros e da comunidade — não localizada
# literalmente numa página narrativa própria do portal (Swagger/referência é
# renderizado em JS, inacessível às ferramentas desta sessão); registrado em
# `.private/validation/sprint-1/CC-105/riot-policy-research.md`.
API_KEY_HEADER = "X-Riot-Token"

# Match-V5/Account-V1 usam roteamento regional (não por plataforma); só a
# região usada pelo recorte deste projeto (BR1 -> Americas, docs/architecture.md)
# é permitida. Nenhuma URL vinda de fora do processo chega a este cliente.
ALLOWED_HOSTS = frozenset({"americas.api.riotgames.com"})

# Mesmo padrão de match ID de docs/contracts.md; a fonte real nunca aceita host,
# URL ou caminho arbitrário de usuário.
_MATCH_ID_PATTERN = re.compile(r"^BR1_[0-9]{1,20}$")

_MAX_BACKOFF_SECONDS = 30.0


@dataclass(frozen=True, slots=True)
class RiotFetchBudget:
    """Teto explícito e local desta execução do spike (CC-105); nunca presume a
    quota real da Riot — só limita o próprio comportamento por cima do que o
    servidor de fato impuser (ex.: Retry-After em 429). max_requests conta
    resolução de conta, paginação e match+timeline juntos (orçamento
    compartilhado, não um teto por chamada)."""

    max_matches: int
    max_requests: int
    max_attempts_per_request: int = 5
    max_total_seconds: float = 300.0
    request_timeout_seconds: float = 10.0


@dataclass(frozen=True, slots=True)
class RawRiotResponse:
    """Corpo bruto de uma chamada bem-sucedida, anterior a qualquer mapeamento
    para os DTOs do CC-104 — essa auditoria depende de dado real (ver plano do
    card) e não é feita por este transporte."""

    match_id: str
    kind: str  # "match" ou "timeline"
    body: bytes
    sha256: str
    fetched_at: datetime


def _backoff_seconds(attempt: int) -> float:
    return min(_MAX_BACKOFF_SECONDS, 0.5 * (2 ** (attempt - 1)))


def _parse_retry_after(raw_value: str | None) -> float | None:
    if raw_value is None:
        return None
    try:
        seconds = float(raw_value)
    except ValueError:
        return None
    return max(0.0, seconds)


def _parse_json_body(kind: str, body: bytes) -> Any:
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise SourceSchemaError(
            code="SCHEMA_UNSUPPORTED", detail=f"corpo de {kind} não é JSON válido"
        ) from exc


class RiotHttpTransport:
    """Cliente HTTP mínimo e sequencial para o spike de auditoria (CC-105) — não é
    o importador durável/worker do CC-201: sem fila, sem persistência em banco,
    sem concorrência. Host fechado à região Americas do Match-V5/Account-V1,
    chave só em header (nunca em URL/log/erro), sem seguir redirect (evita
    vazar a chave para outro host), TLS nunca relaxado, execução sequencial com
    orçamento explícito de requisições/tentativas/tempo — compartilhado entre
    resolução de conta, paginação e download de match+timeline. Toda espera
    (backoff/Retry-After) e todo timeout de requisição respeitam o tempo
    restante do orçamento: nunca dorme além do que resta, encerra retomável em
    vez disso."""

    def __init__(
        self,
        api_key: str,
        budget: RiotFetchBudget,
        client: httpx.AsyncClient | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._api_key = api_key
        self._budget = budget
        self._client = client or httpx.AsyncClient(follow_redirects=False)
        self._owns_client = client is None
        self._sleep = sleep
        self._requests_made = 0
        self._started_at = time.monotonic()

    @property
    def requests_made(self) -> int:
        return self._requests_made

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def fetch_match(self, match_id: str) -> RawRiotResponse:
        self._validate_match_id(match_id)
        body = await self._request_raw("match", f"/lol/match/v5/matches/{match_id}")
        _parse_json_body("match", body)  # valida JSON antes de aceitar o corpo
        return self._to_raw_response(match_id, "match", body)

    async def fetch_timeline(self, match_id: str) -> RawRiotResponse:
        self._validate_match_id(match_id)
        body = await self._request_raw("timeline", f"/lol/match/v5/matches/{match_id}/timeline")
        _parse_json_body("timeline", body)
        return self._to_raw_response(match_id, "timeline", body)

    async def resolve_account_puuid(self, game_name: str, tag_line: str) -> str:
        """Account-V1 by-riot-id; segmentos codificados individualmente (não a
        string inteira) para suportar Unicode/espaço/caractere especial com
        segurança. Nunca loga gameName/tagLine/puuid."""
        path = (
            "/riot/account/v1/accounts/by-riot-id/"
            f"{quote(game_name, safe='')}/{quote(tag_line, safe='')}"
        )
        body = await self._request_raw("account", path)
        document = _parse_json_body("account", body)
        puuid = document.get("puuid") if isinstance(document, dict) else None
        if not isinstance(puuid, str) or not puuid:
            raise SourceSchemaError(
                code="SCHEMA_UNSUPPORTED", detail="resposta de account sem puuid válido"
            )
        return puuid

    async def list_match_ids_by_puuid(
        self, puuid: str, *, queue: int, start: int, count: int
    ) -> list[str]:
        """Só esta conta pode originar sementes (CC-105); o chamador nunca deve
        repassar um puuid vindo de outro lugar. Nunca loga o puuid."""
        path = f"/lol/match/v5/matches/by-puuid/{quote(puuid, safe='')}/ids"
        body = await self._request_raw(
            "match-list",
            path,
            params={"queue": str(queue), "start": str(start), "count": str(count)},
        )
        document = _parse_json_body("match-list", body)
        if not isinstance(document, list) or not all(isinstance(item, str) for item in document):
            raise SourceSchemaError(
                code="SCHEMA_UNSUPPORTED",
                detail="resposta de listagem de partidas com formato inesperado",
            )
        return document

    def _validate_match_id(self, match_id: str) -> None:
        if not _MATCH_ID_PATTERN.match(match_id):
            raise SourceSchemaError(
                code="RIOT_INVALID_MATCH_ID", detail="match_id fora do padrão esperado"
            )

    def _to_raw_response(self, match_id: str, kind: str, body: bytes) -> RawRiotResponse:
        return RawRiotResponse(
            match_id=match_id,
            kind=kind,
            body=body,
            sha256=hashlib.sha256(body).hexdigest(),
            fetched_at=datetime.now(UTC),
        )

    async def _request_raw(
        self, kind: str, path: str, params: dict[str, str] | None = None
    ) -> bytes:
        host = next(iter(ALLOWED_HOSTS))
        url = f"https://{host}{path}"

        attempt = 0
        while True:
            attempt += 1
            remaining = self._remaining_budget_seconds()
            if remaining <= 0:
                raise SourceError(
                    code="UPSTREAM_UNAVAILABLE",
                    detail="orçamento local de tempo desta execução esgotado",
                )
            if self._requests_made >= self._budget.max_requests:
                raise SourceRateLimitedError(
                    code="RIOT_RATE_LIMITED",
                    detail="orçamento local de requisições desta execução esgotado",
                )

            self._requests_made += 1
            request_timeout = min(self._budget.request_timeout_seconds, remaining)
            try:
                response = await self._client.get(
                    url,
                    headers={API_KEY_HEADER: self._api_key},
                    params=params,
                    timeout=request_timeout,
                )
            except httpx.HTTPError as exc:
                if attempt >= self._budget.max_attempts_per_request:
                    raise SourceError(
                        code="UPSTREAM_UNAVAILABLE",
                        detail=f"falha de rede após {attempt} tentativas",
                    ) from exc
                await self._sleep_within_budget(_backoff_seconds(attempt))
                continue

            if response.status_code == 200:
                return response.content
            if response.status_code in (401, 403):
                raise SourceAuthError(
                    code="RIOT_AUTH_FAILED", detail=f"status {response.status_code}"
                )
            if response.status_code == 404:
                raise SourceNotFoundError(code="RIOT_NOT_FOUND", detail=f"{kind} não encontrado")
            if response.status_code == 429:
                retry_after = _parse_retry_after(response.headers.get("Retry-After"))
                if retry_after is None or attempt >= self._budget.max_attempts_per_request:
                    raise SourceRateLimitedError(
                        code="RIOT_RATE_LIMITED",
                        detail="limite de taxa excedido",
                        retry_after_seconds=retry_after,
                    )
                await self._sleep_within_budget(retry_after)
                continue
            if 500 <= response.status_code < 600:
                if attempt >= self._budget.max_attempts_per_request:
                    raise SourceError(
                        code="UPSTREAM_UNAVAILABLE",
                        detail=f"status {response.status_code} após {attempt} tentativas",
                    )
                await self._sleep_within_budget(_backoff_seconds(attempt))
                continue
            raise SourceError(
                code="UPSTREAM_UNAVAILABLE", detail=f"status inesperado {response.status_code}"
            )

    async def _sleep_within_budget(self, seconds: float) -> None:
        """Nunca dorme além do orçamento de tempo restante: se a espera exigida
        (backoff ou Retry-After real) alcançaria ou ultrapassaria o que resta,
        encerra retomável agora em vez de bloquear além do teto local."""
        remaining = self._remaining_budget_seconds()
        if seconds >= remaining:
            raise SourceRateLimitedError(
                code="RIOT_RATE_LIMITED",
                detail="espera necessária excederia o orçamento local de tempo; "
                "encerrando retomável",
                retry_after_seconds=seconds,
            )
        await self._sleep(seconds)

    def _remaining_budget_seconds(self) -> float:
        return self._budget.max_total_seconds - (time.monotonic() - self._started_at)
