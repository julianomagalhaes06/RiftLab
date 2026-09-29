from __future__ import annotations

import json

import httpx
import pytest

from riftlab.ingestion.infrastructure.riot_account import RiotAccountConfig
from riftlab.ingestion.infrastructure.riot_transport import RiotFetchBudget, RiotHttpTransport
from riftlab.ranked.domain import SOLO_QUEUE, account_key, parse_league_entries
from riftlab.shared.errors import SourceAuthError, SourceRateLimitedError, SourceSchemaError

PUUID = "synthetic-puuid"


def entry(tier: str, rank: str, lp: int) -> dict[str, object]:
    return {
        "puuid": PUUID,
        "queueType": SOLO_QUEUE,
        "tier": tier,
        "rank": rank,
        "leaguePoints": lp,
        "wins": 12,
        "losses": 8,
    }


@pytest.mark.parametrize(
    "tier,division,lp",
    [
        ("IRON", "IV", 0),
        ("GOLD", "III", 76),
        ("DIAMOND", "I", 99),
        ("MASTER", "I", 286),
    ],
)
def test_valid_rank(tier: str, division: str, lp: int) -> None:
    result = parse_league_entries([entry(tier, division, lp)], PUUID)[SOLO_QUEUE]
    assert result.tier == tier and result.league_points == lp
    assert result.division == (None if tier == "MASTER" else division)


def test_unranked_and_missing_solo() -> None:
    assert parse_league_entries([], PUUID) == {}
    flex = entry("GOLD", "III", 15)
    flex["queueType"] = "RANKED_FLEX_SR"
    assert SOLO_QUEUE not in parse_league_entries([flex], PUUID)


def test_identity_is_secret_scoped_and_rejects_foreign_puuid() -> None:
    a = RiotAccountConfig("fixture-a", "BR1", "BR1", "AMERICAS")
    b = RiotAccountConfig("fixture-b", "BR1", "BR1", "AMERICAS")
    assert account_key(a, "secret") != account_key(b, "secret")
    assert account_key(a, None) is None
    with pytest.raises(SourceSchemaError):
        parse_league_entries([entry("GOLD", "III", 76)], "another-puuid")
    with pytest.raises(SourceSchemaError):
        parse_league_entries([entry("GOLD", "III", 76)] * 2, PUUID)
    with pytest.raises(SourceSchemaError):
        parse_league_entries([{"puuid": PUUID}], PUUID)


@pytest.mark.asyncio
async def test_fixed_hosts_routes_and_single_account() -> None:
    seen: list[tuple[str, str]] = []

    def responder(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.host, request.url.path))
        if request.url.host == "americas.api.riotgames.com":
            return httpx.Response(200, json={"puuid": PUUID})
        return httpx.Response(200, json=[entry("GOLD", "III", 76)])

    async with httpx.AsyncClient(transport=httpx.MockTransport(responder)) as client:
        transport = RiotHttpTransport("synthetic-key", RiotFetchBudget(0, 2), client=client)
        puuid = await transport.resolve_account_puuid("fixture", "BR1")
        parsed = parse_league_entries(await transport.fetch_league_entries_by_puuid(puuid), puuid)
    assert parsed[SOLO_QUEUE].league_points == 76
    assert seen == [
        ("americas.api.riotgames.com", "/riot/account/v1/accounts/by-riot-id/fixture/BR1"),
        ("br1.api.riotgames.com", f"/lol/league/v4/entries/by-puuid/{PUUID}"),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,expected",
    [(401, SourceAuthError), (403, SourceAuthError), (429, SourceRateLimitedError)],
)
async def test_failure_does_not_become_unranked(status: int, expected: type[Exception]) -> None:
    def responder(_: httpx.Request) -> httpx.Response:
        return httpx.Response(status, headers={"Retry-After": "17"} if status == 429 else {})

    async with httpx.AsyncClient(transport=httpx.MockTransport(responder)) as client:
        transport = RiotHttpTransport(
            "synthetic-key", RiotFetchBudget(0, 1, max_attempts_per_request=1), client=client
        )
        with pytest.raises(expected) as info:
            await transport.fetch_league_entries_by_puuid(PUUID)
        if status == 429:
            assert info.value.retry_after_seconds == 17


@pytest.mark.asyncio
async def test_malformed_league_response() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, content=json.dumps({"entries": []}))
        )
    ) as client:
        transport = RiotHttpTransport("synthetic-key", RiotFetchBudget(0, 1), client=client)
        with pytest.raises(SourceSchemaError):
            await transport.fetch_league_entries_by_puuid(PUUID)
