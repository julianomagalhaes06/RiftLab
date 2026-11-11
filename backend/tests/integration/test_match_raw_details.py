"""Detalhes de placar (feitiços, runas, nível, dano, ouro, visão) lidos do bruto guardado."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from riftlab.ingestion.infrastructure.raw_store import DurableRawMatchStore
from riftlab.matches.raw_details import ParticipantRunesRaw, participant_details

MATCH = "BR1_9212100011"

PERKS = {
    "styles": [
        {
            "description": "primaryStyle",
            "style": 8000,
            "selections": [{"perk": 8008}, {"perk": 9111}],
        },
        {"description": "subStyle", "style": 8300, "selections": [{"perk": 8345}]},
    ]
}


def _store(directory: Path, participants: list[dict], match_id: str = MATCH) -> None:
    document = {"metadata": {"matchId": match_id}, "info": {"participants": participants}}
    body = json.dumps(document).encode()
    DurableRawMatchStore(directory).store(match_id, "match", body, hashlib.sha256(body).hexdigest())


def test_reads_details_and_discards_invalid_values(tmp_path: Path) -> None:
    _store(
        tmp_path,
        [
            {
                "participantId": 1,
                "summoner1Id": 4,
                "summoner2Id": 7,
                "perks": PERKS,
                "champLevel": 17,
                "totalDamageDealtToChampions": 19134,
                "goldEarned": 12034,
                "wardsPlaced": 6,
                "visionScore": 31,
                "largestMultiKill": 3,
            },
            {
                "participantId": 2,
                "summoner1Id": 4,
                "summoner2Id": "7",
                "perks": {"styles": [{"description": "subStyle", "style": 8300}]},
                "champLevel": 99,
                "totalDamageDealtToChampions": -1,
                "goldEarned": True,
                "largestMultiKill": 6,
            },
            {"participantId": 11, "summoner1Id": 4, "summoner2Id": 7},
        ],
    )
    details = participant_details(str(tmp_path), MATCH)
    assert set(details) == {1, 2}
    first = details[1]
    assert first.summoner_spells == (4, 7)
    assert first.runes == ParticipantRunesRaw(
        primary_style=8000, keystone=8008, secondary_style=8300
    )
    assert (first.champ_level, first.damage_to_champions, first.gold_earned) == (17, 19134, 12034)
    assert (first.wards_placed, first.vision_score, first.largest_multi_kill) == (6, 31, 3)
    second = details[2]
    assert second.summoner_spells is None and second.runes is None
    assert second.champ_level is None and second.damage_to_champions is None
    assert second.gold_earned is None and second.largest_multi_kill is None


def test_demo_missing_or_corrupted_raw_returns_nothing(tmp_path: Path) -> None:
    assert participant_details(str(tmp_path), "DEMO-0008") == {}
    assert participant_details(str(tmp_path), "BR1_9212100012") == {}
    _store(tmp_path, [{"participantId": 1, "summoner1Id": 4, "summoner2Id": 7}])
    for blob in (tmp_path / "blobs").iterdir():
        blob.write_bytes(b"{}")
    assert participant_details(str(tmp_path), MATCH) == {}
