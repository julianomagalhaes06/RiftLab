"""CC-1506: coleta de elo alto — ordem da escada, deduplicação, patch e manifesto sem jogador."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from riftlab.collection.apex import ApexPlan, collect_apex, split_of
from riftlab.ingestion.infrastructure.raw_store import DurableRawMatchStore
from riftlab.shared.errors import SourceError


@dataclass
class _Body:
    body: bytes
    sha256: str


def _body(payload: dict) -> _Body:
    raw = json.dumps(payload).encode()
    return _Body(raw, hashlib.sha256(raw).hexdigest())


class FakeTransport:
    def __init__(self) -> None:
        self.requests_made = 0
        self.listed: list[str] = []

    async def fetch_apex_league(self, tier: str) -> list[dict]:
        self.requests_made += 1
        ladder = {
            "CHALLENGER": [
                {"puuid": "puuid-low", "leaguePoints": 900},
                {"puuid": "puuid-top", "leaguePoints": 1500},
            ],
            "GRANDMASTER": [{"puuid": "puuid-gm", "leaguePoints": 400}],
            "MASTER": [],
        }
        return ladder[tier]

    async def fetch_league_division(self, tier: str, division: str, page: int) -> list[dict]:
        self.requests_made += 1
        if division == "I" and page == 1:
            return [
                {"puuid": "puuid-d1b", "leaguePoints": 10},
                {"puuid": "puuid-d1a", "leaguePoints": 80},
            ]
        if division == "II" and page == 1:
            return [{"puuid": "puuid-d2", "leaguePoints": 50}]
        return []

    async def list_match_ids_by_puuid(self, puuid, *, queue, start, count, start_time):
        self.requests_made += 1
        self.listed.append(puuid)
        return {
            "puuid-top": ["BR1_1", "BR1_2"],
            "puuid-low": ["BR1_2", "BR1_3"],
            "puuid-gm": ["BR1_4"],
            "puuid-d1a": ["BR1_5"],
            "puuid-d1b": ["BR1_6"],
            "puuid-d2": ["BR1_7"],
        }[puuid]

    async def fetch_match(self, match_id: str) -> _Body:
        self.requests_made += 1
        version = "16.22.1.1" if match_id == "BR1_3" else "16.23.700.1"
        return _body({"metadata": {"matchId": match_id}, "info": {"gameVersion": version}})

    async def fetch_timeline(self, match_id: str) -> _Body:
        self.requests_made += 1
        return _body({"metadata": {"matchId": match_id}, "info": {"frames": []}})


@pytest.mark.asyncio
async def test_apex_collection_orders_by_points_dedupes_filters_patch_and_keeps_no_player_ids(
    tmp_path: Path,
):
    transport = FakeTransport()
    store = DurableRawMatchStore(tmp_path / "raw")
    manifest = await collect_apex(
        transport, store, ApexPlan("16.23", 1_700_000_000, 10, 20, 100), known={"BR1_4"}
    )
    assert transport.listed == ["puuid-top", "puuid-low", "puuid-gm"]  # maior pontuação primeiro
    stored = sorted(
        manifest["split"]["trainMatchIds"]
        + manifest["split"]["validationMatchIds"]
        + manifest["split"]["testMatchIds"]
    )
    assert stored == ["BR1_1", "BR1_2"]  # BR1_3 é de outro patch; BR1_4 já era conhecido
    assert manifest["counts"]["OTHER_PATCH"] == 1 and manifest["counts"]["DUPLICATE"] == 2
    assert store.read_if_valid("BR1_1", "timeline") is not None
    assert "puuid" not in json.dumps(manifest)
    assert all(split_of(m) == split_of(m) for m in stored)  # determinística


@pytest.mark.asyncio
async def test_second_round_skips_players_already_listed_and_goes_down_to_diamond(
    tmp_path: Path,
):
    transport = FakeTransport()
    store = DurableRawMatchStore(tmp_path / "raw")
    plan = ApexPlan(
        "16.23",
        1_700_000_000,
        3,
        20,
        100,
        ("CHALLENGER", "GRANDMASTER", "MASTER", "DIAMOND"),
        skip_players=2,
    )
    manifest = await collect_apex(transport, store, plan)
    # Elite pela pontuação (top, low, gm); Diamante com divisões intercaladas (d1a, d2, d1b);
    # pula 2.
    assert transport.listed == ["puuid-gm", "puuid-d1a", "puuid-d2"]
    assert manifest["matchTier"] == {
        "BR1_4": "GRANDMASTER",
        "BR1_5": "DIAMOND_I",
        "BR1_7": "DIAMOND_II",
    }
    assert manifest["counts"]["PLAYERS_DIAMOND_I"] == 1 and manifest["skipPlayers"] == 2
    assert "puuid" not in json.dumps(manifest)


class FlakyTransport(FakeTransport):
    """Rede cai em algumas chamadas de partida (como o Wi-Fi às 13h33 de 23/11)."""

    def __init__(self, failures: int) -> None:
        super().__init__()
        self.failures = failures

    async def fetch_match(self, match_id: str) -> _Body:
        if self.failures > 0:
            self.failures -= 1
            raise SourceError(code="UPSTREAM_UNAVAILABLE", detail="falha de rede após 5 tentativas")
        return await super().fetch_match(match_id)


@pytest.mark.asyncio
async def test_network_outage_waits_and_retries_then_stops_with_partial_manifest(tmp_path: Path):
    waits: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        waits.append(seconds)

    plan = ApexPlan("16.23", 1_700_000_000, 10, 20, 100)
    short = await collect_apex(
        FlakyTransport(2), DurableRawMatchStore(tmp_path / "a"), plan, sleep=fake_sleep
    )
    assert waits == [30, 60] and short["counts"]["NETWORK_WAITS"] == 2
    assert short["counts"]["MATCHES_STORED"] == 3 and "STOPPED_NETWORK" not in short["counts"]

    waits.clear()
    long = await collect_apex(
        FlakyTransport(100), DurableRawMatchStore(tmp_path / "b"), plan, sleep=fake_sleep
    )
    assert long["counts"]["STOPPED_NETWORK"] == 1 and len(waits) == 7
    assert long["counts"].get("MATCHES_STORED", 0) == 0 and "split" in long


@pytest.mark.asyncio
async def test_diamond_only_round_spreads_players_across_divisions(tmp_path: Path):
    transport = FakeTransport()
    plan = ApexPlan("16.23", 1_700_000_000, 4, 20, 100, ("DIAMOND",))
    manifest = await collect_apex(transport, DurableRawMatchStore(tmp_path / "raw"), plan)
    assert transport.listed == ["puuid-d1a", "puuid-d2"]  # 1 por divisão; III e IV vazias
    assert manifest["counts"]["PLAYERS_DIAMOND_II"] == 1
