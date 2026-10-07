"""Synthetic CC-316-B partition fixtures shared by adoption and exploration tests.

Everything here is hand-built: no real match IDs, private files or network.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from riftlab.collection.cc316_adoption import derive_adopted_partition

FAKE_AMENDMENT_SHA256 = "ab" * 32


def partition_digest_for(match_id: str) -> str:
    return hashlib.sha256(f"CC-316-partition-v1|{match_id}".encode()).hexdigest()


def source_snapshot() -> dict[str, Any]:
    """Hand-built attempt-4-shaped snapshot: 24 eligible, 6 out of scope."""
    candidates = [f"BR1_{index}" for index in range(1, 31)]
    out_of_scope = candidates[-6:]
    eligible = sorted(
        candidates[:-6], key=lambda match_id: (partition_digest_for(match_id), match_id)
    )
    exploration, tail = eligible[:20], eligible[20:]
    overlap = tail[1::2]
    confirmation = [match_id for match_id in tail if match_id not in overlap]
    return {
        "schemaVersion": 1,
        "protocol": "CC-316-A",
        "createdAt": "2026-10-01T20:23:41.575300+00:00",
        "status": "PARTITION_POTENTIAL_READY",
        "target": {"patch": "16.19", "queueId": 420, "mapId": 11, "gameComplete": True},
        "discovery": {
            "source": "CONSENTED_SEEDS_PLUS_PARTICIPANTS",
            "maximumParticipantHops": 2,
            "maximumMatchesPerPlayer": 4,
            "uniqueCandidateCount": len(candidates),
            "metadataFetchedCount": len(candidates),
            "sourceDepthByMatchId": {
                match_id: 0 if index < 3 else 1 for index, match_id in enumerate(candidates)
            },
            "stopReason": "MAX_CANDIDATES",
        },
        "partition": {
            "hashRule": "sha256(CC-316-partition-v1|matchId), ascending",
            "explorationMatchIds": exploration,
            "confirmationPotentialMatchIds": confirmation,
            "overlapExcludedMatchIds": overlap,
            "structuralExcludedMatchIds": {match_id: "OUT_OF_SCOPE" for match_id in out_of_scope},
            "exclusionCounts": {"OUT_OF_SCOPE": len(out_of_scope)},
        },
        "privacy": {
            "participantPuuidsPersisted": False,
            "participantHmacsPersisted": False,
            "matchBodiesPersisted": False,
            "timelineFetched": False,
        },
        "requests": 417,
        "elapsedSeconds": 517.126,
    }


def encode(document: dict[str, Any]) -> bytes:
    return (json.dumps(document, indent=2, sort_keys=True) + "\n").encode()


def derive(snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    raw = encode(snapshot or source_snapshot())
    return derive_adopted_partition(
        raw,
        source_sha256=hashlib.sha256(raw).hexdigest(),
        amendment_sha256=FAKE_AMENDMENT_SHA256,
    )


def private_dir(path: Path) -> Path:
    path.mkdir(parents=True, mode=0o700, exist_ok=True)
    path.chmod(0o700)
    return path


def write_private_with_sidecar(path: Path, raw: bytes) -> None:
    path.write_bytes(raw)
    path.chmod(0o600)
    sidecar = path.with_suffix(path.suffix + ".sha256")
    sidecar.write_text(f"{hashlib.sha256(raw).hexdigest()}  {path.name}\n", encoding="ascii")
    sidecar.chmod(0o600)


def write_amendment(private: Path) -> str:
    amendment = (
        private / "validation" / "sprint-3" / "CC-316" / "02-amendment-cc316-b-2026-10-07.md"
    )
    private_dir(amendment.parent)
    amendment.write_text("emenda sintética", encoding="utf-8")
    digest = hashlib.sha256(amendment.read_bytes()).hexdigest()
    amendment.with_suffix(".md.sha256").write_text(
        f"{digest}  {amendment.name}\n", encoding="ascii"
    )
    return digest
