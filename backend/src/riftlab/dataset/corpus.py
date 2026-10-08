"""CC-302: freeze the patch corpus and its chronological train/test split.

Reads raw match bodies only (never writes to the stores), rechecks the target
filter, excludes remakes with a declared reason and cuts by game start time so
that no instant is split between train and test. The manifest lists match IDs
only; participant identifiers never leave the raw bodies.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from riftlab.collection.cc316_adoption import canonical_json
from riftlab.collection.cc316_discovery import DiscoveryError, _atomic_write_once
from riftlab.ingestion.infrastructure.raw_store import DurableRawMatchStore

_MATCH_ID = re.compile(r"^BR1_[0-9]{1,20}$")
_CONFIG_KEYS = {
    "schemaVersion",
    "patch",
    "queueId",
    "mapId",
    "excludeRemakes",
    "split",
}


class CorpusError(RuntimeError):
    """Corpus inconsistent with the dataset policy; nothing is written."""


@dataclass(frozen=True, slots=True)
class DatasetPolicy:
    patch: str
    queue_id: int
    map_id: int
    exclude_remakes: bool
    train_fraction: float
    validation_fraction: float = 0.0  # CC-401: 0 mantém a divisão treino/teste do CC-302


def load_dataset_policy(path: Path) -> DatasetPolicy:
    try:
        document: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError, UnicodeDecodeError) as exc:
        raise CorpusError("política do dataset ilegível") from exc
    if not isinstance(document, dict) or set(document) != _CONFIG_KEYS:
        raise CorpusError("política do dataset com campos divergentes")
    split = document["split"]
    if (
        document["schemaVersion"] != 1
        or not isinstance(document["patch"], str)
        or not isinstance(split, dict)
        or set(split) - {"validationFraction"} != {"method", "trainFraction"}
        or split["method"] != "CHRONOLOGICAL_BY_MATCH"
        or not isinstance(split["trainFraction"], float)
        or not isinstance(split.get("validationFraction", 0.0), float)
        or not 0 < split["trainFraction"] < 1
        or not 0 <= split.get("validationFraction", 0.0)
        or split["trainFraction"] + split.get("validationFraction", 0.0) > 1 - 1e-6
        or not isinstance(document["excludeRemakes"], bool)
    ):
        raise CorpusError("política do dataset inválida")
    return DatasetPolicy(
        patch=document["patch"],
        queue_id=int(document["queueId"]),
        map_id=int(document["mapId"]),
        exclude_remakes=document["excludeRemakes"],
        train_fraction=split["trainFraction"],
        validation_fraction=split.get("validationFraction", 0.0),
    )


def _complete_pairs(directory: Path) -> list[str]:
    return sorted(
        ref.name.removesuffix(".match.ref")
        for ref in directory.glob("*.match.ref")
        if _MATCH_ID.fullmatch(ref.name.removesuffix(".match.ref"))
        and (directory / ref.name.replace(".match.ref", ".timeline.ref")).exists()
    )


def _exclusion(
    info: Mapping[str, Any], match_id: str, document: Any, policy: DatasetPolicy
) -> str | None:
    metadata = document.get("metadata") if isinstance(document, dict) else None
    if not isinstance(metadata, dict) or metadata.get("matchId") != match_id:
        raise CorpusError("identidade de partida divergente no armazenamento")
    version = info.get("gameVersion")
    if (
        not isinstance(version, str)
        or ".".join(version.split(".")[:2]) != policy.patch
        or info.get("queueId") != policy.queue_id
        or info.get("mapId") != policy.map_id
        or info.get("endOfGameResult") != "GameComplete"
    ):
        raise CorpusError("partida fora do alvo no armazenamento; corpus recusado")
    participants = info.get("participants")
    if policy.exclude_remakes and isinstance(participants, list):
        if any(isinstance(p, dict) and p.get("gameEndedInEarlySurrender") for p in participants):
            return "REMAKE"
    return None


def build_corpus_manifest(
    sources: Mapping[str, Path], policy: DatasetPolicy, *, policy_sha256: str
) -> dict[str, object]:
    """Return the canonical manifest; fail closed on overlap, identity or target drift."""
    if not sources:
        raise CorpusError("nenhuma fonte de partidas")
    seen: dict[str, str] = {}
    starts: dict[str, int] = {}
    exclusions: dict[str, str] = {}
    source_counts: dict[str, dict[str, int]] = {}
    for label, directory in sorted(sources.items()):
        store = DurableRawMatchStore(directory, create=False)
        ids = _complete_pairs(directory)
        if not ids:
            raise CorpusError(f"fonte {label} sem pares completos")
        counts: Counter[str] = Counter()
        for match_id in ids:
            if match_id in seen:
                raise CorpusError("partida repetida entre fontes")
            seen[match_id] = label
            body = store.read_if_valid_read_only(match_id, "match")
            if body is None:
                raise CorpusError("par completo sem corpo legível")
            document = json.loads(body)
            info = document.get("info") if isinstance(document, dict) else None
            if not isinstance(info, dict):
                raise CorpusError("corpo de partida sem info")
            reason = _exclusion(info, match_id, document, policy)
            counts["pairs"] += 1
            if reason is not None:
                exclusions[match_id] = reason
                counts[f"excluded{reason.title()}"] += 1
                continue
            start = info.get("gameStartTimestamp") or info.get("gameCreation")
            if not isinstance(start, int) or isinstance(start, bool) or start <= 0:
                raise CorpusError("partida sem instante de início")
            starts[match_id] = start
            counts["kept"] += 1
        source_counts[label] = dict(sorted(counts.items()))

    ordered = sorted(starts, key=lambda match_id: (starts[match_id], match_id))
    if len(ordered) < 2:
        raise CorpusError("corpus pequeno demais para dividir")
    boundary = starts[ordered[int(len(ordered) * policy.train_fraction)]]
    train = [match_id for match_id in ordered if starts[match_id] < boundary]
    test = [match_id for match_id in ordered if starts[match_id] >= boundary]
    if not train or not test:
        raise CorpusError("divisão cronológica degenerada")
    if policy.validation_fraction:
        end = policy.train_fraction + policy.validation_fraction
        test_boundary = starts[ordered[int(len(ordered) * end)]]
        validation = [m for m in test if starts[m] < test_boundary]
        test = [m for m in test if starts[m] >= test_boundary]
        if not validation or not test:
            raise CorpusError("divisão cronológica degenerada")
        return _three_way_manifest(
            policy,
            policy_sha256,
            source_counts,
            exclusions,
            (boundary, test_boundary),
            (train, validation, test),
        )
    manifest: dict[str, object] = {
        "schemaVersion": 1,
        "card": "CC-302",
        "patch": policy.patch,
        "target": {"queueId": policy.queue_id, "mapId": policy.map_id, "gameComplete": True},
        "policySha256": policy_sha256,
        "sources": source_counts,
        "exclusions": dict(sorted(exclusions.items())),
        "split": {
            "method": "CHRONOLOGICAL_BY_MATCH",
            "trainFraction": policy.train_fraction,
            "boundaryGameStartMs": boundary,
            "rule": "train: gameStart < boundary; test: gameStart >= boundary",
            "trainMatchIds": train,
            "testMatchIds": test,
        },
        "counts": {"kept": len(ordered), "train": len(train), "test": len(test)},
        "privacy": {"participantIdentifiersInManifest": False, "storesOpenedReadOnly": True},
    }
    return manifest


def _three_way_manifest(
    policy: DatasetPolicy,
    policy_sha256: str,
    source_counts: dict[str, dict[str, int]],
    exclusions: dict[str, str],
    boundaries: tuple[int, int],
    splits: tuple[list[str], list[str], list[str]],
) -> dict[str, object]:
    train, validation, test = splits
    return {
        "schemaVersion": 2,
        "card": "CC-401",
        "patch": policy.patch,
        "target": {"queueId": policy.queue_id, "mapId": policy.map_id, "gameComplete": True},
        "policySha256": policy_sha256,
        "sources": source_counts,
        "exclusions": dict(sorted(exclusions.items())),
        "split": {
            "method": "CHRONOLOGICAL_BY_MATCH",
            "trainFraction": policy.train_fraction,
            "validationFraction": policy.validation_fraction,
            "boundaryGameStartMs": boundaries[0],
            "testBoundaryGameStartMs": boundaries[1],
            "rule": "train < boundary <= validation < testBoundary <= test (gameStart)",
            "trainMatchIds": train,
            "validationMatchIds": validation,
            "testMatchIds": test,
        },
        "counts": {
            "kept": len(train) + len(validation) + len(test),
            "train": len(train),
            "validation": len(validation),
            "test": len(test),
        },
        "privacy": {"participantIdentifiersInManifest": False, "storesOpenedReadOnly": True},
    }


def freeze_corpus(
    output: Path, sources: Mapping[str, Path], policy_path: Path
) -> tuple[dict[str, object], str]:
    """Write the manifest once (with SHA-256 sidecar) under `.private`."""
    if not any(part == ".private" for part in output.parts):
        raise CorpusError("manifesto do corpus deve ficar dentro de .private")
    if output.exists():
        raise CorpusError("manifesto já congelado; não será sobrescrito")
    policy = load_dataset_policy(policy_path)
    policy_sha256 = hashlib.sha256(policy_path.read_bytes()).hexdigest()
    manifest = build_corpus_manifest(sources, policy, policy_sha256=policy_sha256)
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        digest = _atomic_write_once(output, canonical_json(manifest))
    except DiscoveryError as exc:
        raise CorpusError(str(exc)) from exc
    return manifest, digest
