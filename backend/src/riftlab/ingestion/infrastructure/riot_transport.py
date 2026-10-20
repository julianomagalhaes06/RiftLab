from __future__ import annotations

import asyncio
import errno
import hashlib
import json
import re
import socket
import ssl
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

import httpcore
import httpx

from riftlab.ingestion.infrastructure.riot_rate_limiter import RiotRateLimiter
from riftlab.observability import quiet_http_client_logs
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

# Match-V5/Account-V1 usam Americas; League-V4 usa BR1 (CC-310). Ambos
# fechados no código; nenhuma URL de entrada pública chega a este cliente.
AMERICAS_HOST = "americas.api.riotgames.com"
BR1_HOST = "br1.api.riotgames.com"
ALLOWED_HOSTS = frozenset({AMERICAS_HOST, BR1_HOST})

# Mesmo padrão de match ID de docs/contracts.md; a fonte real nunca aceita host,
# URL ou caminho arbitrário de usuário.
_MATCH_ID_PATTERN = re.compile(r"^BR1_[0-9]{1,20}$")

_MAX_BACKOFF_SECONDS = 30.0
_MAX_DIAGNOSTIC_CAUSE_DEPTH = 5
_DIAGNOSTIC_TLS_ERROR_TYPES = (
    ssl.SSLError,
    ssl.SSLCertVerificationError,
    ssl.SSLZeroReturnError,
    ssl.SSLEOFError,
    ssl.SSLWantReadError,
    ssl.SSLWantWriteError,
    ssl.SSLSyscallError,
)
_DIAGNOSTIC_SOCKET_ERROR_TYPES = (
    ConnectionError,
    ConnectionRefusedError,
    ConnectionResetError,
    ConnectionAbortedError,
    BrokenPipeError,
    TimeoutError,
)


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
    stop_on_429: bool = False


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


def _diagnostic_result_for_status(status_code: int) -> str:
    """Classify a numeric HTTP status into the CC-320 diagnostic allowlist."""
    if status_code == 200:
        return "HTTP_200"
    if status_code in (401, 403):
        return "HTTP_AUTH"
    if status_code == 404:
        return "HTTP_NOT_FOUND"
    if status_code == 429:
        return "HTTP_429"
    if 500 <= status_code < 600:
        return "HTTP_5XX"
    return "HTTP_UNEXPECTED"


def _diagnostic_exception_class(exc: httpx.HTTPError) -> str:
    """Return one fixed class label; never serialize exception text or context."""
    allowlisted_types = (
        (httpx.ConnectTimeout, "ConnectTimeout"),
        (httpx.ReadTimeout, "ReadTimeout"),
        (httpx.WriteTimeout, "WriteTimeout"),
        (httpx.PoolTimeout, "PoolTimeout"),
        (httpx.ConnectError, "ConnectError"),
        (httpx.ReadError, "ReadError"),
        (httpx.WriteError, "WriteError"),
        (httpx.ProxyError, "ProxyError"),
        (httpx.RemoteProtocolError, "RemoteProtocolError"),
        (httpx.LocalProtocolError, "LocalProtocolError"),
        (httpx.ProtocolError, "ProtocolError"),
    )
    for exception_type, safe_name in allowlisted_types:
        if isinstance(exc, exception_type):
            return safe_name
    return "HTTPError"


def _diagnostic_cause_category(exc: BaseException) -> str:
    """Classify allowlisted causal types/codes without serializing exception data."""
    current = exc.__cause__ if exc.__cause__ is not None else exc.__context__
    seen: set[int] = set()
    for _ in range(_MAX_DIAGNOSTIC_CAUSE_DEPTH):
        if current is None or id(current) in seen:
            break
        seen.add(id(current))

        if type(current) is socket.gaierror:
            if current.errno in {socket.EAI_AGAIN, socket.EAI_NONAME, socket.EAI_FAIL}:
                return "DNS"
        elif type(current) in _DIAGNOSTIC_TLS_ERROR_TYPES:
            return "TLS"
        elif type(current) in (httpx.ProxyError, httpcore.ProxyError):
            return "PROXY"
        elif type(current) in _DIAGNOSTIC_SOCKET_ERROR_TYPES:
            return "SOCKET"
        elif type(current) is OSError and current.errno in {
            errno.ECONNABORTED,
            errno.ECONNREFUSED,
            errno.ECONNRESET,
            errno.EHOSTUNREACH,
            errno.ENETUNREACH,
            errno.ETIMEDOUT,
            errno.EPIPE,
        }:
            return "SOCKET"

        current = current.__cause__ if current.__cause__ is not None else current.__context__
    return "UNKNOWN"


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
    sem concorrência. Hosts fechados em Americas (Match/Account) e BR1 (League),
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
        rate_limiter: RiotRateLimiter | None = None,
        on_request: Callable[[str], int] | None = None,
    ) -> None:
        quiet_http_client_logs()  # a URL da Riot carrega Riot ID/PUUID: nunca no log
        self._api_key = api_key
        self._budget = budget
        # CC-206: limitação GLOBAL opcional (janelas de app/método, contagem do
        # servidor e Retry-After para todas as chamadas). O worker do CC-201 não
        # passa nenhum: o job é o dono do retry lá.
        self._rate_limiter = rate_limiter
        self._client = client or httpx.AsyncClient(follow_redirects=False)
        self._owns_client = client is None
        self._sleep = sleep
        self._on_request = on_request
        self._on_request_result: (
            Callable[[int, str, int | None, float, str | None, str | None], None] | None
        ) = None
        self._on_local_budget: Callable[[str, str], None] | None = None
        self._requests_made = 0
        self._status_counts: dict[str, int] = {}
        self._started_at = time.monotonic()

    @property
    def requests_made(self) -> int:
        return self._requests_made

    def status_counts(self) -> dict[str, int]:
        """Respostas HTTP por 'tipo:status' (ex.: 'match:200', 'list:429'): diagnóstico
        agregado do comportamento real, sem nenhum identificador."""
        return dict(sorted(self._status_counts.items()))

    def set_request_observer(self, observer: Callable[[str], int]) -> None:
        """Attach a durable budget reservation before any request is sent."""
        if self._requests_made:
            raise ValueError("observador de orçamento deve ser instalado antes da primeira chamada")
        self._on_request = observer

    def set_request_result_observer(
        self, observer: Callable[[int, str, int | None, float, str | None, str | None], None]
    ) -> None:
        """Persist allowlisted outcome after each HTTP attempt returns."""
        if self._requests_made:
            raise ValueError("observador de resultado deve ser instalado antes da primeira chamada")
        self._on_request_result = observer

    def set_local_budget_observer(self, observer: Callable[[str, str], None]) -> None:
        """Record a typed local-budget stop without reserving an HTTP request."""
        if self._requests_made:
            raise ValueError("observador de orçamento deve ser instalado antes da primeira chamada")
        self._on_local_budget = observer

    def set_request_limit(self, limit: int) -> None:
        """Lower a transport's request ceiling before its first request."""
        if self._requests_made:
            raise ValueError("teto deve ser configurado antes da primeira chamada")
        if limit < 1 or limit > self._budget.max_requests:
            raise ValueError("teto de requisições fora do orçamento configurado")
        self._budget = RiotFetchBudget(
            max_matches=self._budget.max_matches,
            max_requests=limit,
            max_attempts_per_request=self._budget.max_attempts_per_request,
            max_total_seconds=self._budget.max_total_seconds,
            request_timeout_seconds=self._budget.request_timeout_seconds,
            stop_on_429=self._budget.stop_on_429,
        )

    def set_total_time_limit(self, seconds: float) -> None:
        """Lower this run's transport timer to the remaining cumulative budget."""
        if self._requests_made:
            raise ValueError("teto de tempo deve ser configurado antes da primeira chamada")
        if seconds <= 0 or seconds > self._budget.max_total_seconds:
            raise ValueError("teto de tempo fora do orçamento configurado")
        self._budget = RiotFetchBudget(
            max_matches=self._budget.max_matches,
            max_requests=self._budget.max_requests,
            max_attempts_per_request=self._budget.max_attempts_per_request,
            max_total_seconds=seconds,
            request_timeout_seconds=self._budget.request_timeout_seconds,
            stop_on_429=self._budget.stop_on_429,
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def fetch_match(self, match_id: str) -> RawRiotResponse:
        self._validate_match_id(match_id)
        body = await self._request_raw("match", f"/lol/match/v5/matches/{match_id}")
        _parse_json_body("match", body)  # valida JSON antes de aceitar o corpo
        return self._to_raw_response(match_id, "match", body)

    async def fetch_match_for_structural_projection(self, match_id: str) -> bytes:
        """Return opaque Match-V5 bytes for the CC-316 allowlist scanner only.

        This deliberately does not JSON-decode, hash, or wrap the response in a
        raw-storage object. The caller must scan it and discard the bytes without
        writing them to disk.
        """
        self._validate_match_id(match_id)
        return await self._request_raw("match-structural", f"/lol/match/v5/matches/{match_id}")

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

    async def resolve_account(self, game_name: str, tag_line: str) -> tuple[str, str, str]:
        """Account-V1 by-riot-id com o Riot ID canônico: (puuid, gameName, tagLine). Mesma
        codificação por segmento de `resolve_account_puuid`; nunca loga a identidade."""
        path = (
            "/riot/account/v1/accounts/by-riot-id/"
            f"{quote(game_name, safe='')}/{quote(tag_line, safe='')}"
        )
        document = _parse_json_body("account", await self._request_raw("account", path))
        if not isinstance(document, dict):
            raise SourceSchemaError(
                code="SCHEMA_UNSUPPORTED", detail="resposta de account sem identidade válida"
            )
        puuid, name, tag = (document.get(key) for key in ("puuid", "gameName", "tagLine"))
        if not (
            isinstance(puuid, str)
            and puuid
            and isinstance(name, str)
            and name
            and isinstance(tag, str)
            and tag
        ):
            raise SourceSchemaError(
                code="SCHEMA_UNSUPPORTED", detail="resposta de account sem identidade válida"
            )
        return puuid, name, tag

    async def fetch_summoner_by_puuid(self, puuid: str) -> tuple[int, int]:
        """Summoner-V4 por PUUID no host fixo BR1: (profileIconId, summonerLevel)."""
        if not puuid or "/" in puuid or "?" in puuid:
            raise SourceSchemaError(code="SCHEMA_UNSUPPORTED", detail="puuid inválido")
        body = await self._request_raw(
            "summoner",
            f"/lol/summoner/v4/summoners/by-puuid/{quote(puuid, safe='')}",
            host=BR1_HOST,
        )
        document = _parse_json_body("summoner", body)
        icon = document.get("profileIconId") if isinstance(document, dict) else None
        level = document.get("summonerLevel") if isinstance(document, dict) else None
        if not all(
            isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in (icon, level)
        ):
            raise SourceSchemaError(
                code="SCHEMA_UNSUPPORTED", detail="resposta de summoner com formato inesperado"
            )
        return icon, level  # type: ignore[return-value]

    async def fetch_league_entries_by_puuid(self, puuid: str) -> list[Any]:
        """League-V4 por PUUID no host fixo BR1; nunca devolve URL/headers."""
        if not puuid or "/" in puuid or "?" in puuid:
            raise SourceSchemaError(code="SCHEMA_UNSUPPORTED", detail="puuid inválido")
        body = await self._request_raw(
            "league", f"/lol/league/v4/entries/by-puuid/{quote(puuid, safe='')}", host=BR1_HOST
        )
        document = _parse_json_body("league", body)
        if not isinstance(document, list):
            raise SourceSchemaError(
                code="SCHEMA_UNSUPPORTED", detail="resposta de league com formato inesperado"
            )
        return document

    async def list_match_ids_by_puuid(
        self,
        puuid: str,
        *,
        queue: int | None,
        start: int,
        count: int,
        start_time: int | None = None,
        end_time: int | None = None,
        match_type: str | None = None,
    ) -> list[str]:
        """Só esta conta pode originar sementes (CC-105); o chamador nunca deve
        repassar um puuid vindo de outro lugar. Nunca loga o puuid.
        `start_time`/`end_time` (epoch em segundos) restringem a janela de datas da
        listagem (CC-206); sem eles a chamada é idêntica à anterior. `queue=None` com
        `match_type` (ex.: "ranked") lista várias filas de uma vez."""
        path = f"/lol/match/v5/matches/by-puuid/{quote(puuid, safe='')}/ids"
        params = {"start": str(start), "count": str(count)}
        if queue is not None:
            params["queue"] = str(queue)
        if match_type is not None:
            params["type"] = match_type
        if start_time is not None:
            params["startTime"] = str(start_time)
        if end_time is not None:
            params["endTime"] = str(end_time)
        body = await self._request_raw("match-list", path, params=params)
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
        self,
        kind: str,
        path: str,
        params: dict[str, str] | None = None,
        *,
        host: str = AMERICAS_HOST,
    ) -> bytes:
        if host not in ALLOWED_HOSTS:
            raise SourceSchemaError(code="SCHEMA_UNSUPPORTED", detail="host fora da allowlist")
        url = f"https://{host}{path}"

        attempt = 0
        while True:
            attempt += 1
            remaining = self._remaining_budget_seconds()
            if remaining <= 0:
                if self._on_local_budget is not None:
                    self._on_local_budget(kind, "LOCAL_TIME_BUDGET")
                raise SourceError(
                    code="UPSTREAM_UNAVAILABLE",
                    detail="orçamento local de tempo desta execução esgotado",
                )
            if self._requests_made >= self._budget.max_requests:
                if self._on_local_budget is not None:
                    self._on_local_budget(kind, "LOCAL_REQUEST_BUDGET")
                raise SourceRateLimitedError(
                    code="RIOT_RATE_LIMITED",
                    detail="orçamento local de requisições desta execução esgotado",
                )

            if self._rate_limiter is not None:
                # Espera (ou encerra retomável) ANTES de gastar uma requisição do
                # orçamento; nunca dorme além do tempo que resta.
                await self._rate_limiter.acquire(kind, max_wait_seconds=remaining)
                remaining = self._remaining_budget_seconds()
                if remaining <= 0:
                    if self._on_local_budget is not None:
                        self._on_local_budget(kind, "LOCAL_TIME_BUDGET")
                    raise SourceError(
                        code="UPSTREAM_UNAVAILABLE",
                        detail="orçamento local de tempo desta execução esgotado",
                    )

            request_ordinal: int | None = None
            if self._on_request is not None:
                request_ordinal = self._on_request(kind)
            self._requests_made += 1
            request_timeout = min(self._budget.request_timeout_seconds, remaining)
            request_started_at = time.monotonic()
            try:
                response = await self._client.get(
                    url,
                    headers={API_KEY_HEADER: self._api_key},
                    params=params,
                    timeout=request_timeout,
                )
            except httpx.HTTPError as exc:
                duration_ms = max(0.0, (time.monotonic() - request_started_at) * 1000)
                if request_ordinal is not None and self._on_request_result is not None:
                    self._on_request_result(
                        request_ordinal,
                        "HTTP_ERROR",
                        None,
                        duration_ms,
                        _diagnostic_exception_class(exc),
                        _diagnostic_cause_category(exc),
                    )
                if attempt >= self._budget.max_attempts_per_request:
                    raise SourceError(
                        code="UPSTREAM_UNAVAILABLE",
                        detail=f"falha de rede após {attempt} tentativas",
                    ) from exc
                await self._sleep_within_budget(_backoff_seconds(attempt))
                continue

            key = f"{kind}:{response.status_code}"
            self._status_counts[key] = self._status_counts.get(key, 0) + 1
            if request_ordinal is not None and self._on_request_result is not None:
                self._on_request_result(
                    request_ordinal,
                    _diagnostic_result_for_status(response.status_code),
                    response.status_code,
                    max(0.0, (time.monotonic() - request_started_at) * 1000),
                    None,
                    None,
                )
            if self._rate_limiter is not None:
                self._rate_limiter.observe(kind, response.headers)
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
                if retry_after is not None and self._rate_limiter is not None:
                    self._rate_limiter.penalize(retry_after)  # vale para TODAS as chamadas
                if (
                    self._budget.stop_on_429
                    or retry_after is None
                    or attempt >= self._budget.max_attempts_per_request
                ):
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
