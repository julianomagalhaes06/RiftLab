"""CC-316-B adoption of the attempt-4 partition: derivation, pinning and CLI.

Fixtures are synthetic; no test touches the real private collection directory.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import typer

import riftlab.collection.cc316_adoption as adoption_module
from riftlab.cli.collect import collect_cc316_adopt_partition
from riftlab.collection.cc316_adoption import (
    derive_adopted_partition,
    load_adopted_exploration_ids,
    persist_adopted_partition_once,
    validate_adopted_partition,
)
from riftlab.collection.cc316_discovery import DiscoveryError
from support.cc316_partition import (
    FAKE_AMENDMENT_SHA256,
    derive,
    encode,
    partition_digest_for,
    private_dir,
    source_snapshot,
    write_amendment,
    write_private_with_sidecar,
)


def test_derived_partition_recomputes_digests_and_preserves_assignment() -> None:
    source = source_snapshot()
    artifact = derive(source)

    source_partition = source["partition"]
    partition = artifact["partition"]
    assert partition["explorationMatchIds"] == source_partition["explorationMatchIds"]
    assert (
        partition["confirmationPotentialMatchIds"]
        == source_partition["confirmationPotentialMatchIds"]
    )
    assert partition["overlapExcludedMatchIds"] == source_partition["overlapExcludedMatchIds"]
    assert partition["outOfScopeMatchIds"] == sorted(source_partition["structuralExcludedMatchIds"])
    assert len(artifact["candidates"]) == 30
    for match_id, record in artifact["candidates"].items():
        assert record["partitionDigest"] == partition_digest_for(match_id)
        expected_source = (
            "CONSENTED_SEED_MATCH_LIST" if record["depth"] == 0 else "PARTICIPANT_MATCH_LIST"
        )
        assert record["source"] == expected_source
    assert artifact["universe"] == {
        "definition": "ATTEMPT_4_ADMITTED_CANDIDATES",
        "candidateCount": 30,
        "eligibleCount": 24,
        "outOfScopeCount": 6,
        "depthCounts": {"0": 3, "1": 27},
        "stopReason": "MAX_CANDIDATES",
        "maximumMatchesPerPlayer": 4,
        "maximumParticipantHops": 2,
        "unadmittedListedIdsRecorded": False,
    }
    assert artifact["discoveryClosed"] is True
    assert artifact["amendment"]["sha256"] == FAKE_AMENDMENT_SHA256
    assert validate_adopted_partition(artifact) == tuple(source_partition["explorationMatchIds"])


@pytest.mark.parametrize(
    "corrupt",
    [
        "exploration_not_first_20",
        "overlapping_categories",
        "missing_candidate_depth",
        "extra_root_field",
        "privacy_flag",
        "other_exclusion_reason",
        "depth_out_of_range",
        "unsorted_confirmation",
        "overlap_out_of_order",
        "status_not_ready",
        "candidate_count_mismatch",
    ],
)
def test_derivation_fails_closed_on_corrupted_source(corrupt: str) -> None:
    source = source_snapshot()
    discovery, partition = source["discovery"], source["partition"]
    exploration = partition["explorationMatchIds"]
    if corrupt == "exploration_not_first_20":
        confirmation = partition["confirmationPotentialMatchIds"]
        exploration[0], confirmation[0] = confirmation[0], exploration[0]
    elif corrupt == "overlapping_categories":
        partition["confirmationPotentialMatchIds"].append(exploration[0])
    elif corrupt == "missing_candidate_depth":
        discovery["sourceDepthByMatchId"].pop(exploration[0])
        discovery["uniqueCandidateCount"] -= 1
        discovery["metadataFetchedCount"] -= 1
    elif corrupt == "extra_root_field":
        source["timeline"] = []
    elif corrupt == "privacy_flag":
        source["privacy"]["participantHmacsPersisted"] = True
    elif corrupt == "other_exclusion_reason":
        first = next(iter(partition["structuralExcludedMatchIds"]))
        partition["structuralExcludedMatchIds"][first] = "MATCH_NOT_FOUND"
    elif corrupt == "depth_out_of_range":
        discovery["sourceDepthByMatchId"][exploration[0]] = 3
    elif corrupt == "unsorted_confirmation":
        partition["confirmationPotentialMatchIds"].reverse()
    elif corrupt == "overlap_out_of_order":
        partition["overlapExcludedMatchIds"].reverse()
    elif corrupt == "status_not_ready":
        source["status"] = "NO_SAMPLE"
    else:
        discovery["uniqueCandidateCount"] += 1

    with pytest.raises(DiscoveryError, match="partição CC-316-B inválida"):
        derive(source)


def test_derivation_rejects_source_hash_mismatch() -> None:
    raw = encode(source_snapshot())
    with pytest.raises(DiscoveryError, match="hash do snapshot fonte divergente"):
        derive_adopted_partition(
            raw, source_sha256="0" * 64, amendment_sha256=FAKE_AMENDMENT_SHA256
        )


def test_adopted_partition_is_private_write_once_and_pinned(tmp_path: Path) -> None:
    output_dir = private_dir(tmp_path / ".private" / "collection" / "cc316")
    artifact = derive()
    path = output_dir / "partition-cc316b-adopted.json"

    digest = persist_adopted_partition_once(path, artifact)

    assert path.stat().st_mode & 0o777 == 0o600
    assert path.with_suffix(".json.sha256").read_text() == f"{digest}  {path.name}\n"
    exploration = load_adopted_exploration_ids(path, expected_sha256=digest)
    assert exploration == tuple(artifact["partition"]["explorationMatchIds"])
    hidden = set(artifact["partition"]["confirmationPotentialMatchIds"]) | set(
        artifact["partition"]["overlapExcludedMatchIds"]
    )
    assert hidden.isdisjoint(exploration)
    with pytest.raises(DiscoveryError, match="já existe"):
        persist_adopted_partition_once(path, artifact)
    for wrong in ("0" * 64, ""):
        with pytest.raises(DiscoveryError, match="valor fixado"):
            load_adopted_exploration_ids(path, expected_sha256=wrong)
    path.chmod(0o644)
    with pytest.raises(DiscoveryError, match="inseguro"):
        load_adopted_exploration_ids(path, expected_sha256=digest)


def test_loader_rejects_noncanonical_tampered_or_unsidecarred_artifact(tmp_path: Path) -> None:
    output_dir = private_dir(tmp_path / ".private" / "collection" / "cc316")
    artifact = derive()

    compact = json.dumps(artifact, sort_keys=True).encode()
    compact_path = output_dir / "compact.json"
    write_private_with_sidecar(compact_path, compact)
    with pytest.raises(DiscoveryError, match="serialização não canônica"):
        load_adopted_exploration_ids(
            compact_path, expected_sha256=hashlib.sha256(compact).hexdigest()
        )

    first_confirmation = artifact["partition"]["confirmationPotentialMatchIds"][0]
    artifact["candidates"][first_confirmation]["assignment"] = "EXPLORATION"
    tampered = encode(artifact)
    tampered_path = output_dir / "tampered.json"
    write_private_with_sidecar(tampered_path, tampered)
    with pytest.raises(DiscoveryError, match="registro de candidato incoerente"):
        load_adopted_exploration_ids(
            tampered_path, expected_sha256=hashlib.sha256(tampered).hexdigest()
        )

    tampered_path.with_suffix(".json.sha256").write_text(
        f"{'0' * 64}  {tampered_path.name}\n", encoding="ascii"
    )
    with pytest.raises(DiscoveryError, match="sidecar"):
        load_adopted_exploration_ids(
            tampered_path, expected_sha256=hashlib.sha256(tampered).hexdigest()
        )


def test_cli_adopt_partition_writes_once_and_prints_counts_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    output_dir = private_dir(tmp_path / ".private" / "collection" / "cc316")
    raw = encode(source_snapshot())
    (output_dir / "partition-snapshot.json").write_bytes(raw)
    monkeypatch.setattr(adoption_module, "SOURCE_SNAPSHOT_SHA256", hashlib.sha256(raw).hexdigest())
    monkeypatch.setattr(adoption_module, "AMENDMENT_SHA256", write_amendment(tmp_path / ".private"))

    collect_cc316_adopt_partition(output_dir=output_dir)

    printed = json.loads(capsys.readouterr().out)
    assert "BR1_" not in json.dumps(printed)
    assert {key: printed[key] for key in printed if key != "adoptedPartitionSha256"} == {
        "candidateCount": 30,
        "eligibleCount": 24,
        "explorationCount": 20,
        "confirmationPotentialCount": 2,
        "overlapExcludedCount": 2,
        "outOfScopeCount": 6,
    }
    adopted = output_dir / "partition-cc316b-adopted.json"
    assert hashlib.sha256(adopted.read_bytes()).hexdigest() == printed["adoptedPartitionSha256"]
    with pytest.raises(typer.Exit):
        collect_cc316_adopt_partition(output_dir=output_dir)
