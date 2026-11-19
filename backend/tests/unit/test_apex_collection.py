"""CC-1506: coleta de elo alto — ordem da escada, deduplicação, patch e manifesto sem jogador."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from riftlab.collection.apex import ApexPlan, collect_apex, split_of
from riftlab.ingestion.infrastructure.raw_store import DurableRawMatchStore


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

    async def list_match_ids_by_puuid(self, puuid, *, queue, start, count, start_time):
        self.requests_made += 1
        self.listed.append(puuid)
        return {
            "puuid-top": ["BR1_1", "BR1_2"],
            "puuid-low": ["BR1_2", "BR1_3"],
            "puuid-gm": ["BR1_4"],
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
