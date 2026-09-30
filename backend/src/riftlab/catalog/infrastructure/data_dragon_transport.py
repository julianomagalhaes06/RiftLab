"""Cliente HTTP mínimo para o CDN público do Data Dragon (CC-202) — sem
autenticação (é um CDN público, diferente da API de partidas), host fechado,
sem redirect, orçamento explícito de tempo/tentativas. Mesma disciplina de
riot_transport.py (CC-105), adaptada: sem chave, sem paginação."""

from __future__ import annotations

import asyncio
import hashlib
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import httpx

from riftlab.shared.errors import SourceError, SourceNotFoundError, SourceSchemaError

# Único host permitido — nenhuma URL vinda de configuração ou usuário chega a
# este cliente (docs/architecture.md: "Riot adapter só acessa hosts
# permitidos, jamais URL arbitrária").
ALLOWED_HOST = "ddragon.leagueoflegends.com"

# Mesmo formato usado por configs/catalog-mappings.yaml e pela API real de
# versions.json (ex.: "16.17.1") — nunca aceita um valor arbitrário como
# parte da URL sem validar o formato primeiro.
VERSION_PATTERN = re.compile(r"^\d{1,3}\.\d{1,3}\.\d{1,3}$")

_MAX_BACKOFF_SECONDS = 10.0

# Idiomas aceitos para champion.json (CC-208): lista fechada, nunca texto livre.
ALLOWED_CHAMPION_LOCALES = frozenset({"pt_BR"})


def _check_version(data_dragon_version: str) -> None:
    if not VERSION_PATTERN.match(data_dragon_version):
        raise SourceSchemaError(
            code="DATA_DRAGON_INVALID_VERSION",
            detail="versão do Data Dragon fora do formato esperado",
        )


@dataclass(frozen=True, slots=True)
class DataDragonFetchBudget:
    max_attempts: int = 3
    max_total_seconds: float = 30.0
    request_timeout_seconds: float = 10.0


@dataclass(frozen=True, slots=True)
class RawDataDragonResponse:
    data_dragon_version: str
    body: bytes
    sha256: str


def _backoff_seconds(attempt: int) -> float:
    return min(_MAX_BACKOFF_SECONDS, 0.5 * (2 ** (attempt - 1)))


class DataDragonTransport:
    """Busca versions.json público e item/champion.json de versão exata.
    Host e caminhos são fixos; nenhuma URL vem do usuário."""

    def __init__(
        self,
        budget: DataDragonFetchBudget | None = None,
        client: httpx.AsyncClient | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._budget = budget or DataDragonFetchBudget()
        self._client = client or httpx.AsyncClient(follow_redirects=False)
        self._owns_client = client is None
        self._sleep = sleep
        self._started_at = time.monotonic()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def fetch_item_catalog(self, data_dragon_version: str) -> RawDataDragonResponse:
        _check_version(data_dragon_version)
        url = f"https://{ALLOWED_HOST}/cdn/{data_dragon_version}/data/en_US/item.json"
        return await self._get(url, data_dragon_version)

    async def fetch_versions(self) -> RawDataDragonResponse:
        return await self._get(f"https://{ALLOWED_HOST}/api/versions.json", "versions.json")

    async def fetch_champion_catalog(
        self, data_dragon_version: str, locale: str = "pt_BR"
    ) -> RawDataDragonResponse:
        """champion.json (CC-208) de uma versão validada e de um idioma da lista
        fechada — rota própria e fixa, ao lado da de itens (nenhuma das duas aceita
        caminho vindo de fora)."""
        _check_version(data_dragon_version)
        if locale not in ALLOWED_CHAMPION_LOCALES:
            raise SourceSchemaError(
                code="DATA_DRAGON_INVALID_LOCALE", detail="idioma fora da lista permitida"
            )
        url = f"https://{ALLOWED_HOST}/cdn/{data_dragon_version}/data/{locale}/champion.json"
        return await self._get(url, data_dragon_version)

    async def _get(self, url: str, data_dragon_version: str) -> RawDataDragonResponse:
        attempt = 0
        while True:
            attempt += 1
            remaining = self._budget.max_total_seconds - (time.monotonic() - self._started_at)
            if remaining <= 0:
                raise SourceError(
                    code="UPSTREAM_UNAVAILABLE",
                    detail="orçamento local de tempo desta sincronização esgotado",
                )
            timeout = min(self._budget.request_timeout_seconds, remaining)
            try:
                response = await self._client.get(url, timeout=timeout)
            except httpx.HTTPError as exc:
                if attempt >= self._budget.max_attempts:
                    raise SourceError(
                        code="UPSTREAM_UNAVAILABLE",
                        detail=f"falha de rede após {attempt} tentativas",
                    ) from exc
                await self._sleep(_backoff_seconds(attempt))
                continue

            if response.status_code == 200:
                body = response.content
                return RawDataDragonResponse(
                    data_dragon_version=data_dragon_version,
                    body=body,
                    sha256=hashlib.sha256(body).hexdigest(),
                )
            if response.status_code == 404:
                raise SourceNotFoundError(
                    code="DATA_DRAGON_VERSION_NOT_FOUND",
                    detail=f"versão {data_dragon_version!r} não encontrada no Data Dragon",
                )
            if 500 <= response.status_code < 600:
                if attempt >= self._budget.max_attempts:
                    raise SourceError(
                        code="UPSTREAM_UNAVAILABLE",
                        detail=f"status {response.status_code} após {attempt} tentativas",
                    )
                await self._sleep(_backoff_seconds(attempt))
                continue
            raise SourceError(
                code="UPSTREAM_UNAVAILABLE", detail=f"status inesperado {response.status_code}"
            )
