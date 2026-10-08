"""CC-401: freeze the final dataset from frozen rows and the three-way corpus manifest.

Rows keep their content; only the split label is taken from the manifest. The
artifact records the hashes of both inputs, aggregate coverage (split ×
champion × main item ordinal) and player contamination: how many validation
and test rows belong to a player who also appears in a training match. PUUIDs
are read from the raw match bodies in memory only and never written.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from riftlab.dataset.rows import split_assignment
from riftlab.ingestion.infrastructure.raw_store import DurableRawMatchStore

SPLITS = ("train", "validation", "test")


class FinalDatasetError(RuntimeError):
    """Rows and manifest disagree; nothing is written."""


def _players(match_id: str, stores: Sequence[DurableRawMatchStore]) -> dict[int, str]:
    for store in stores:
        try:
            body = store.read_if_valid_read_only(match_id, "match")
        except ValueError:
            continue
        if body is not None:
            info = json.loads(body)["info"]
            return {int(p["participantId"]): str(p["puuid"]) for p in info["participants"]}
    raise FinalDatasetError("partida do dataset sem corpo bruto")


def build_final_dataset(
    rows_artifact: Mapping[str, Any],
    manifest: Mapping[str, Any],
    raw_directories: Sequence[Path],
    *,
    rows_sha256: str,
    manifest_sha256: str,
) -> dict[str, Any]:
    split_of = split_assignment(manifest)
    if set(split_of.values()) != set(SPLITS):
        raise FinalDatasetError("manifesto sem as três partes treino/validação/teste")
    rows_in = rows_artifact.get("rows")
    if not isinstance(rows_in, list):
        raise FinalDatasetError("artefato de linhas sem lista de linhas")
    stores = [DurableRawMatchStore(d, create=False) for d in raw_directories]
    rows: list[dict[str, Any]] = []
    for row in rows_in:
        split = split_of.get(row["matchId"])
        if split is None:
            raise FinalDatasetError("linha de partida fora do corpus")
        rows.append({**row, "split": split})
    train_players = {
        puuid
        for match_id, split in split_of.items()
        if split == "train"
        for puuid in _players(match_id, stores).values()
    }
    coverage: dict[str, Counter[str]] = defaultdict(Counter)
    contamination: dict[str, Counter[str]] = defaultdict(Counter)
    matches: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        split = row["split"]
        matches[split].add(row["matchId"])
        coverage[split][f"champion:{row['championId']}|ordinal:{row['mainItemOrdinal']}"] += 1
        coverage[split]["rows"] += 1
        coverage[split]["targetLegal"] += int(bool(row.get("targetLegal")))
        if split != "train":
            player = _players(row["matchId"], stores)[row["participantId"]]
            contamination[split]["rows"] += 1
            contamination[split]["playerSeenInTrain"] += int(player in train_players)
    return {
        "schemaVersion": 1,
        "card": "CC-401",
        "patch": manifest.get("patch"),
        "inputs": {
            "rowsSha256": rows_sha256,
            "corpusManifestSha256": manifest_sha256,
            "rulesVersion": rows_artifact.get("rulesVersion"),
        },
        "coverage": {
            split: {"matchesWithRows": len(matches[split]), **dict(sorted(coverage[split].items()))}
            for split in SPLITS
        },
        "playerContamination": {split: dict(contamination[split]) for split in SPLITS[1:]},
        "rows": rows,
    }
