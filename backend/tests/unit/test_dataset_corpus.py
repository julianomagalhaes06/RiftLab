"""CC-302 corpus freeze: policy, remake exclusion, chronological split and write-once.

Synthetic raw stores only; no network, database or real private data.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from riftlab.dataset.corpus import (
    CorpusError,
    build_corpus_manifest,
    freeze_corpus,
    load_dataset_policy,
)
from riftlab.ingestion.infrastructure.raw_store import DurableRawMatchStore

POLICY_TEXT = """schemaVersion: 1
patch: "16.19"
queueId: 420
mapId: 11
excludeRemakes: true
split:
  method: CHRONOLOGICAL_BY_MATCH
  trainFraction: 0.7
"""


def _body(match_id: str, start: int, **overrides: Any) -> bytes:
    info: dict[str, Any] = {
        "gameVersion": "16.19.715.1",
        "queueId": 420,
        "mapId": 11,
        "endOfGameResult": "GameComplete",
        "gameStartTimestamp": start,
        "participants": [
            {
                "puuid": f"puuid-{match_id}",
                "gameEndedInEarlySurrender": overrides.pop("remake", False),
            }
        ],
    }
    info.update(overrides)
    return json.dumps({"metadata": {"matchId": match_id}, "info": info}).encode()


def _store(directory: Path, matches: dict[str, bytes]) -> Path:
    store = DurableRawMatchStore(directory)
    for match_id, body in matches.items():
        timeline = json.dumps({"metadata": {"matchId": match_id}}).encode()
        for kind, raw in (("match", body), ("timeline", timeline)):
            store.store(match_id, kind, raw, hashlib.sha256(raw).hexdigest())
    return directory


def _policy(tmp_path: Path, text: str = POLICY_TEXT) -> Path:
    path = tmp_path / "dataset.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def _tree(directory: Path) -> dict[str, tuple[int, int]]:
    return {
        str(p.relative_to(directory)): (p.stat().st_size, p.stat().st_mtime_ns)
        for p in sorted(directory.rglob("*"))
    }


def test_split_is_chronological_excludes_remakes_and_never_splits_an_instant(
    tmp_path: Path,
) -> None:
    a = _store(
        tmp_path / "a",
        {f"BR1_{i}": _body(f"BR1_{i}", 1_000 + i) for i in range(1, 7)}
        | {"BR1_50": _body("BR1_50", 1_003, remake=True)},
    )
    b = _store(
        tmp_path / "b",
        {"BR1_20": _body("BR1_20", 1_008), "BR1_21": _body("BR1_21", 1_008)}
        | {"BR1_22": _body("BR1_22", 1_009), "BR1_23": _body("BR1_23", 1_010)},
    )
    before = {name: _tree(path) for name, path in (("a", a), ("b", b))}
    policy = load_dataset_policy(_policy(tmp_path))

    manifest = build_corpus_manifest({"a": a, "b": b}, policy, policy_sha256="0" * 64)

    split = manifest["split"]
    assert isinstance(split, dict)
    # 10 kept, cut index 7 -> start 1008; both matches at 1008 go to test together.
    assert split["boundaryGameStartMs"] == 1_008
    assert split["trainMatchIds"] == [f"BR1_{i}" for i in range(1, 7)]
    assert split["testMatchIds"] == ["BR1_20", "BR1_21", "BR1_22", "BR1_23"]
    assert manifest["exclusions"] == {"BR1_50": "REMAKE"}
    assert manifest["counts"] == {"kept": 10, "train": 6, "test": 4}
    assert manifest["sources"] == {
        "a": {"excludedRemake": 1, "kept": 6, "pairs": 7},
        "b": {"kept": 4, "pairs": 4},
    }
    assert "puuid" not in json.dumps(manifest)
    assert {name: _tree(path) for name, path in (("a", a), ("b", b))} == before


@pytest.mark.parametrize(
    "overrides", [{"gameVersion": "16.18.700.1"}, {"queueId": 400}, {"endOfGameResult": "Abort"}]
)
def test_off_target_match_refuses_the_corpus(tmp_path: Path, overrides: dict[str, Any]) -> None:
    store = _store(
        tmp_path / "a", {"BR1_1": _body("BR1_1", 1), "BR1_2": _body("BR1_2", 2, **overrides)}
    )

    with pytest.raises(CorpusError, match="fora do alvo"):
        build_corpus_manifest(
            {"a": store}, load_dataset_policy(_policy(tmp_path)), policy_sha256="0" * 64
        )


def test_match_in_two_sources_refuses_the_corpus(tmp_path: Path) -> None:
    a = _store(tmp_path / "a", {"BR1_1": _body("BR1_1", 1), "BR1_2": _body("BR1_2", 2)})
    b = _store(tmp_path / "b", {"BR1_2": _body("BR1_2", 2)})

    with pytest.raises(CorpusError, match="repetida"):
        build_corpus_manifest(
            {"a": a, "b": b}, load_dataset_policy(_policy(tmp_path)), policy_sha256="0" * 64
        )


@pytest.mark.parametrize(
    "text",
    [
        POLICY_TEXT.replace("CHRONOLOGICAL_BY_MATCH", "RANDOM"),
        POLICY_TEXT.replace("0.7", "1.5"),
        POLICY_TEXT + "extra: 1\n",
        "- not a mapping\n",
    ],
)
def test_invalid_policy_fails_closed(tmp_path: Path, text: str) -> None:
    with pytest.raises(CorpusError):
        load_dataset_policy(_policy(tmp_path, text))


def test_freeze_writes_once_under_private_with_sidecar(tmp_path: Path) -> None:
    store = _store(tmp_path / "a", {f"BR1_{i}": _body(f"BR1_{i}", i) for i in range(1, 5)})
    output = tmp_path / ".private" / "dataset" / "corpus.json"
    policy = _policy(tmp_path)

    manifest, digest = freeze_corpus(output, {"a": store}, policy)

    raw = output.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == digest
    assert output.with_suffix(".json.sha256").read_text() == f"{digest}  corpus.json\n"
    assert output.stat().st_mode & 0o777 == 0o600
    assert manifest["policySha256"] == hashlib.sha256(policy.read_bytes()).hexdigest()
    with pytest.raises(CorpusError, match="já congelado"):
        freeze_corpus(output, {"a": store}, policy)
    with pytest.raises(CorpusError, match=".private"):
        freeze_corpus(tmp_path / "public.json", {"a": store}, policy)


def _repo_config(*parts: str) -> Path:
    here = Path(__file__).resolve()
    candidates = [root / "configs" / Path(*parts) for root in (here.parents[2], here.parents[3])]
    found = next((path for path in candidates if path.is_file()), None)  # container (/app) ou host
    assert found is not None, f"configs/{'/'.join(parts)} não encontrado"
    return found


def test_repository_policy_is_valid() -> None:
    policy = load_dataset_policy(_repo_config("dataset.yaml"))

    assert (policy.patch, policy.queue_id, policy.map_id) == ("16.19", 420, 11)
    assert policy.exclude_remakes is True
    assert policy.train_fraction == 0.7


def test_three_way_split_is_chronological_and_keeps_instants_together(tmp_path: Path) -> None:
    store = _store(
        tmp_path / "a",
        {f"BR1_{i}": _body(f"BR1_{i}", 1_000 + i) for i in range(1, 15)}
        | {"BR1_99": _body("BR1_99", 1_012)},
    )
    policy = load_dataset_policy(
        _policy(
            tmp_path,
            POLICY_TEXT.replace(
                "  trainFraction: 0.7\n", "  trainFraction: 0.7\n  validationFraction: 0.15\n"
            ),
        )
    )

    manifest = build_corpus_manifest({"a": store}, policy, policy_sha256="0" * 64)

    split = manifest["split"]
    assert isinstance(split, dict)
    train, validation, test = (split[f"{n}MatchIds"] for n in ("train", "validation", "test"))
    assert manifest["schemaVersion"] == 2 and manifest["card"] == "CC-401"
    assert set(train).isdisjoint(validation) and set(validation).isdisjoint(test)
    assert len(train) + len(validation) + len(test) == 15
    # 15 kept: cut at index 10 (start 1011) and index 12 (start 1012, shared by two matches).
    assert split["boundaryGameStartMs"] == 1_011 and split["testBoundaryGameStartMs"] == 1_012
    assert {"BR1_12", "BR1_99"} <= set(test)


@pytest.mark.parametrize("extra", ["  validationFraction: 0.3\n", "  validationFraction: 1\n"])
def test_invalid_validation_fraction_fails_closed(tmp_path: Path, extra: str) -> None:
    with pytest.raises(CorpusError):
        load_dataset_policy(
            _policy(
                tmp_path,
                POLICY_TEXT.replace("  trainFraction: 0.7\n", "  trainFraction: 0.7\n" + extra),
            )
        )


def test_repository_experiment_policy_is_70_15_15() -> None:
    policy = load_dataset_policy(_repo_config("experiments", "dataset-16.19-v2.yaml"))

    assert (policy.train_fraction, policy.validation_fraction) == (0.7, 0.15)
