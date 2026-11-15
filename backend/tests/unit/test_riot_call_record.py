"""Bastidores: cada tentativa HTTP à Riot é registrada só com tipo, região, resultado e tempo."""

from __future__ import annotations

import httpx
import pytest

from riftlab.backstage import calls as riot_calls
from riftlab.ingestion.infrastructure.riot_transport import RiotFetchBudget, RiotHttpTransport
from riftlab.shared.errors import SourceAuthError

PUUID = "synthetic-puuid-for-call-record"


@pytest.fixture
def recorded():
    seen: list[riot_calls.RiotCall] = []
    riot_calls.install(seen.append)
    yield seen
    riot_calls.install(None)


def _transport(responder) -> tuple[RiotHttpTransport, httpx.AsyncClient]:
    client = httpx.AsyncClient(transport=httpx.MockTransport(responder))
    transport = RiotHttpTransport(
        "synthetic-key", RiotFetchBudget(0, 5), client=client, sleep=_no_sleep
    )
    return transport, client


@pytest.mark.asyncio
async def test_each_attempt_is_recorded_without_identifiers(recorded):
    def responder(request: httpx.Request) -> httpx.Response:
        if "/riot/account/" in request.url.path:
            return httpx.Response(200, json={"puuid": PUUID})
        return httpx.Response(401)

    transport, client = _transport(responder)
    try:
        assert await transport.resolve_account_puuid("Nome Sintetico", "BR1") == PUUID
        with pytest.raises(SourceAuthError):
            await transport.fetch_league_entries_by_puuid(PUUID)
    finally:
        await client.aclose()

    assert [(c.kind, c.region, c.outcome, c.status_code, c.attempt) for c in recorded] == [
        ("account", "AMERICAS", "HTTP_200", 200, 1),
        ("league", "BR1", "HTTP_AUTH", 401, 1),
    ]
    assert all(c.duration_ms >= 0 for c in recorded)
    # Nenhum identificador chega ao registro.
    text = repr(recorded)
    assert PUUID not in text and "Nome" not in text and "synthetic-key" not in text


@pytest.mark.asyncio
async def test_network_error_is_recorded_and_a_broken_sink_never_breaks_the_request():
    seen: list[riot_calls.RiotCall] = []

    def broken(call: riot_calls.RiotCall) -> None:
        seen.append(call)
        raise RuntimeError("registro fora do ar")

    riot_calls.install(broken)
    attempts = 0

    def responder(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ConnectError("sem rede", request=request)
        return httpx.Response(200, json={"puuid": PUUID})

    transport, client = _transport(responder)
    try:
        assert await transport.resolve_account_puuid("Nome", "BR1") == PUUID
    finally:
        await client.aclose()
        riot_calls.install(None)
    assert attempts == 2
    assert [(c.kind, c.outcome, c.status_code, c.attempt) for c in seen] == [
        ("account", "NETWORK_ERROR", None, 1),
        ("account", "HTTP_200", 200, 2),
    ]


async def _no_sleep(_seconds: float) -> None:
    return None


def test_without_a_sink_record_does_nothing():
    riot_calls.install(None)
    riot_calls.record(
        kind="match",
        region="AMERICAS",
        outcome="HTTP_200",
        status_code=200,
        duration_ms=12.4,
        attempt=1,
    )
