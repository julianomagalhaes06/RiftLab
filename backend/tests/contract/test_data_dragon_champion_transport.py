"""Rota de champion.json do DataDragonTransport (CC-208) via httpx.MockTransport:
host fechado, caminho fixo, sem redirect, sem credencial, nenhuma rede real."""

from __future__ import annotations

import httpx
import pytest

from riftlab.catalog.infrastructure.data_dragon_transport import (
    DataDragonFetchBudget,
    DataDragonTransport,
)
from riftlab.shared.errors import SourceError, SourceNotFoundError, SourceSchemaError


async def _no_sleep(_: float) -> None:
    return None


def _transport(handler) -> tuple[DataDragonTransport, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(record), follow_redirects=False)
    budget = DataDragonFetchBudget(max_attempts=2, max_total_seconds=5.0)
    return DataDragonTransport(budget=budget, client=client, sleep=_no_sleep), seen


async def test_fetches_the_fixed_champion_route_of_the_pinned_version_without_credentials() -> None:
    transport, seen = _transport(lambda r: httpx.Response(200, content=b'{"type":"champion"}'))
    raw = await transport.fetch_champion_catalog("16.17.1", "pt_BR")
    [request] = seen
    assert (
        str(request.url)
        == "https://ddragon.leagueoflegends.com/cdn/16.17.1/data/pt_BR/champion.json"
    )
    assert request.method == "GET"
    assert not {"authorization", "x-riot-token", "cookie"} & {h.lower() for h in request.headers}
    assert raw.data_dragon_version == "16.17.1" and len(raw.sha256) == 64


@pytest.mark.parametrize("version", ["latest", "16.17", "16.17.1/../../x", "https://evil"])
async def test_invalid_version_never_reaches_the_network(version: str) -> None:
    transport, seen = _transport(lambda r: httpx.Response(200))
    with pytest.raises(SourceSchemaError):
        await transport.fetch_champion_catalog(version, "pt_BR")
    assert seen == []


@pytest.mark.parametrize("locale", ["en_US", "pt_BR/../x", "", "pt-BR"])
async def test_locale_outside_the_closed_list_never_reaches_the_network(locale: str) -> None:
    transport, seen = _transport(lambda r: httpx.Response(200))
    with pytest.raises(SourceSchemaError) as exc:
        await transport.fetch_champion_catalog("16.17.1", locale)
    assert exc.value.code == "DATA_DRAGON_INVALID_LOCALE" and seen == []


async def test_redirect_is_not_followed() -> None:
    transport, seen = _transport(
        lambda r: httpx.Response(302, headers={"location": "https://evil.example/champion.json"})
    )
    with pytest.raises(SourceError):
        await transport.fetch_champion_catalog("16.17.1", "pt_BR")
    assert [str(r.url.host) for r in seen] == ["ddragon.leagueoflegends.com"]


async def test_missing_version_is_not_found_and_5xx_retries_then_fails_as_unavailable() -> None:
    transport, _ = _transport(lambda r: httpx.Response(404))
    with pytest.raises(SourceNotFoundError):
        await transport.fetch_champion_catalog("16.99.1", "pt_BR")
    transport, seen = _transport(lambda r: httpx.Response(503))
    with pytest.raises(SourceError) as exc:
        await transport.fetch_champion_catalog("16.17.1", "pt_BR")
    assert exc.value.code == "UPSTREAM_UNAVAILABLE" and len(seen) == 2


async def test_item_route_is_unchanged_by_the_refactor() -> None:
    transport, seen = _transport(lambda r: httpx.Response(200, content=b"{}"))
    await transport.fetch_item_catalog("16.17.1")
    assert (
        str(seen[0].url) == "https://ddragon.leagueoflegends.com/cdn/16.17.1/data/en_US/item.json"
    )
