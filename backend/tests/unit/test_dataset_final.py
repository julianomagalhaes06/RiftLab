"""CC-401 final dataset: split labels from the manifest, coverage and player contamination."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from riftlab.dataset.final import FinalDatasetError, build_final_dataset
from riftlab.ingestion.infrastructure.raw_store import DurableRawMatchStore


def _store(directory: Path, players: dict[str, list[str]]) -> Path:
    store = DurableRawMatchStore(directory)
    for match_id, puuids in players.items():
        body = json.dumps(
            {
                "metadata": {"matchId": match_id},
                "info": {
                    "participants": [
                        {"participantId": i + 1, "puuid": p} for i, p in enumerate(puuids)
                    ]
                },
            }
        ).encode()
        store.store(match_id, "match", body, hashlib.sha256(body).hexdigest())
    return directory


MANIFEST = {
    "patch": "16.19",
    "split": {
        "trainMatchIds": ["BR1_1"],
        "validationMatchIds": ["BR1_2"],
        "testMatchIds": ["BR1_3"],
    },
}


def _row(match_id: str, participant: int, champion: int = 51) -> dict:
    return {
        "matchId": match_id,
        "split": "train",
        "participantId": participant,
        "championId": champion,
        "mainItemOrdinal": 1,
        "targetItemId": 3031,
        "candidateItemIds": [3031],
        "targetLegal": True,
    }


def test_labels_coverage_and_contamination_without_identifiers(tmp_path: Path) -> None:
    raw = _store(
        tmp_path / "raw",
        {"BR1_1": ["p-a", "p-b"], "BR1_2": ["p-a", "p-c"], "BR1_3": ["p-d", "p-b"]},
    )
    rows = {
        "rulesVersion": "item_rules_16_19_v1",
        "rows": [_row("BR1_1", 1), _row("BR1_2", 1), _row("BR1_2", 2), _row("BR1_3", 2, 81)],
    }

    artifact = build_final_dataset(
        rows, MANIFEST, [raw], rows_sha256="r" * 64, manifest_sha256="m" * 64
    )

    assert [r["split"] for r in artifact["rows"]] == ["train", "validation", "validation", "test"]
    assert artifact["playerContamination"] == {
        "validation": {"rows": 2, "playerSeenInTrain": 1},
        "test": {"rows": 1, "playerSeenInTrain": 1},
    }
    assert artifact["coverage"]["test"]["champion:81|ordinal:1"] == 1
    summary = json.dumps({k: artifact[k] for k in ("coverage", "playerContamination")})
    assert "p-" not in summary


def test_row_outside_the_corpus_or_two_way_manifest_is_refused(tmp_path: Path) -> None:
    raw = _store(tmp_path / "raw", {"BR1_1": ["p-a"], "BR1_2": ["p-b"], "BR1_3": ["p-c"]})
    with pytest.raises(FinalDatasetError):
        build_final_dataset(
            {"rows": [_row("BR1_9", 1)]}, MANIFEST, [raw], rows_sha256="", manifest_sha256=""
        )
    two_way = {"split": {"trainMatchIds": ["BR1_1"], "testMatchIds": ["BR1_3"]}}
    with pytest.raises(FinalDatasetError):
        build_final_dataset({"rows": []}, two_way, [raw], rows_sha256="", manifest_sha256="")
