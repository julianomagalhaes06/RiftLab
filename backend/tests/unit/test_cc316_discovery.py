from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest
import typer

import riftlab.collection.cc316_discovery as discovery_module
from riftlab.cli.collect import collect_structural_cc316
from riftlab.collection.cc316_discovery import (
    BudgetJournal,
    DiscoveryError,
    ProcessLifecycleJournal,
    create_operational_budget_baseline,
    current_boot_id,
    load_operational_budget_baseline,
    persist_snapshot_once,
    process_lifecycle_path,
    require_candidate_budget_resolved,
    run_cc316_discovery,
    temporal_budget_baseline_path,
    validate_prior_budget_history,
    validate_snapshot_contract,
)
from riftlab.collection.seeds import Seed
from riftlab.ingestion.infrastructure.riot_account import RiotAccountConfig
from riftlab.shared.errors import SourceAuthError, SourceNotFoundError, SourceRateLimitedError


@pytest.fixture(autouse=True)
def synthetic_discovery_has_resolved_candidate_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    """Let fake-transport discovery tests exercise the core without authorizing real I/O."""
    monkeypatch.setattr(discovery_module, "require_candidate_budget_resolved", lambda: None)


def _body(match_id: str, participants: list[str]) -> bytes:
    return json.dumps(
        {
            "metadata": {"matchId": match_id, "participants": participants},
            "info": {
                "gameVersion": "16.19.1",
                "queueId": 420,
                "mapId": 11,
                "endOfGameResult": "GameComplete",
                "participants": [{"win": True, "items": [9999]}],
                "teams": [{"win": True}],
            },
            "timeline": {"events": [{"type": "ITEM_PURCHASED", "itemId": 9999}]},
        }
    ).encode()


@dataclass
class FakeTransport:
    players_to_matches: dict[str, list[str]]
    bodies: dict[str, bytes]
    fail_kind: str | None = None
    requests_made: int = 0
    observer: Callable[[str], int] | None = None
    result_observer: (
        Callable[[int, str, int | None, float, str | None, str | None], None] | None
    ) = None
    request_limit: int = 1_000
    time_limit_seconds: float = 3_600.0
    list_calls: list[tuple[str, int, int, int]] | None = None
    match_calls: list[str] | None = None
    # Riot honors `count`; False simulates a listing that returns extra IDs.
    honor_count: bool = False

    def __post_init__(self) -> None:
        self.list_calls = []
        self.match_calls = []

    def set_request_observer(self, observer: Callable[[str], int]) -> None:
        self.observer = observer

    def set_request_result_observer(
        self,
        observer: Callable[[int, str, int | None, float, str | None, str | None], None],
    ) -> None:
        self.result_observer = observer

    def set_local_budget_observer(self, observer: Callable[[str, str], None]) -> None:
        return None

    def set_request_limit(self, limit: int) -> None:
        assert self.requests_made == 0
        self.request_limit = limit

    def set_total_time_limit(self, seconds: float) -> None:
        assert self.requests_made == 0
        assert seconds > 0
        self.time_limit_seconds = seconds

    def _request(
        self, request_class: str, result: str = "HTTP_200", status_code: int | None = 200
    ) -> None:
        if self.requests_made >= self.request_limit:
            raise DiscoveryError("request cap exceeded")
        if self.observer is None:
            raise AssertionError("request observer was not installed")
        ordinal = self.observer(request_class)
        self.requests_made += 1
        if self.result_observer is not None:
            self.result_observer(ordinal, result, status_code, 0.0, None, None)

    async def resolve_account_puuid(self, game_name: str, tag_line: str) -> str:
        if self.fail_kind == "auth":
            self._request("account", "HTTP_AUTH", 403)
            raise SourceAuthError(code="RIOT_AUTH_FAILED", detail="unauthorized")
        if self.fail_kind == "not-found-seed":
            self._request("account", "HTTP_NOT_FOUND", 404)
            raise SourceNotFoundError(code="RIOT_NOT_FOUND", detail="not found")
        self._request("account")
        return "seed-player"

    async def list_match_ids_by_puuid(
        self,
        puuid: str,
        *,
        queue: int,
        start: int,
        count: int,
        start_time: int | None = None,
        end_time: int | None = None,
    ) -> list[str]:
        assert self.list_calls is not None
        self.list_calls.append((puuid, queue, start, count))
        if self.fail_kind == "rate":
            self._request("match-list", "HTTP_429", 429)
            raise SourceRateLimitedError(
                code="RIOT_RATE_LIMITED", detail="limited", retry_after_seconds=2
            )
        self._request("match-list")
        returned = self.players_to_matches.get(puuid, [])
        return returned[:count] if self.honor_count else returned

    async def fetch_match_for_structural_projection(self, match_id: str) -> bytes:
        assert self.match_calls is not None
        self.match_calls.append(match_id)
        if self.fail_kind == "missing-match":
            self._request("match-structural", "HTTP_NOT_FOUND", 404)
            raise SourceNotFoundError(code="RIOT_NOT_FOUND", detail="not found")
        self._request("match-structural")
        return self.bodies[match_id]


def _seed() -> Seed:
    return Seed(
        label="seed-01",
        account=RiotAccountConfig(
            game_name="Fake", tag_line="TEST", platform="BR1", regional_routing="AMERICAS"
        ),
    )


def _private_paths(tmp_path: Path) -> tuple[Path, Path]:
    private = tmp_path / ".private"
    private.mkdir(mode=0o700)
    return private / "cc316-snapshot.json", private / "cc316-budget.json"


def _baseline_test_path(tmp_path: Path) -> Path:
    directory = tmp_path / ".private" / "validation" / "CC-320"
    directory.mkdir(parents=True, mode=0o700)
    directory.chmod(0o700)
    return directory / "baseline-option-b.json"


def _rewrite_baseline_fixture(path: Path, state: dict[str, object]) -> None:
    content = (json.dumps(state, sort_keys=True, separators=(",", ":")) + "\n").encode()
    path.write_bytes(content)
    path.chmod(0o600)
    digest = hashlib.sha256(content).hexdigest()
    sidecar = path.with_suffix(path.suffix + ".sha256")
    sidecar.write_text(f"{digest}  {path.name}\n", encoding="ascii")
    sidecar.chmod(0o600)


def _synthetic_snapshot_contract() -> dict[str, object]:
    """Hand-built contract fixture, independent of discovery/partition producers."""
    candidates = sorted(f"BR1_{index}" for index in range(1, 21))
    digests = {
        match_id: hashlib.sha256(f"CC-316-partition-v1|{match_id}".encode()).hexdigest()
        for match_id in candidates
    }
    exploration = sorted(candidates, key=lambda match_id: (digests[match_id], match_id))
    return {
        "schemaVersion": 1,
        "protocol": "CC-316-A",
        "createdAt": "2026-10-02T00:00:00+00:00",
        "status": "PARTITION_POTENTIAL_READY",
        "target": {"patch": "16.19", "queueId": 420, "mapId": 11, "gameComplete": True},
        "discovery": {
            "source": "CONSENTED_SEEDS_PLUS_PARTICIPANTS",
            "maximumParticipantHops": 2,
            "maximumMatchesPerPlayer": 4,
            "uniqueCandidateCount": len(candidates),
            "metadataRequestAttemptCount": len(candidates),
            "structuralMetadataValidCount": len(candidates),
            "stopReason": None,
            "candidateProvenanceByMatchId": {
                match_id: {"depth": 0, "source": "CONSENTED_SEED_MATCH_LIST"}
                for match_id in candidates
            },
            "metadataRequestAttemptedMatchIds": candidates.copy(),
            "structuralMetadataValidMatchIds": candidates.copy(),
            "metadataNotAttemptedMatchIds": [],
            "metadataUnknownReasonByMatchId": {},
        },
        "partition": {
            "hashRule": "sha256(CC-316-partition-v1|matchId), ascending",
            "partitionDigestByMatchId": digests,
            "candidateMatchIds": candidates,
            "explorationMatchIds": exploration,
            "confirmationPotentialMatchIds": [],
            "overlapExcludedMatchIds": [],
            "usable": True,
            "structuralExcludedMatchIds": {},
            "exclusionCounts": {},
        },
        "privacy": {
            "participantPuuidsPersisted": False,
            "participantHmacsPersisted": False,
            "matchBodiesPersisted": False,
            "timelineFetched": False,
        },
        "requests": 20,
        "elapsedSeconds": 1.0,
    }


def _synthetic_incomplete_snapshot_contract() -> dict[str, object]:
    artifact = _synthetic_snapshot_contract()
    discovery = artifact["discovery"]
    partition = artifact["partition"]
    assert isinstance(discovery, dict) and isinstance(partition, dict)
    match_id = "BR1_21"
    candidates = partition["candidateMatchIds"]
    assert isinstance(candidates, list)
    candidates.append(match_id)
    candidates.sort()
    partition["partitionDigestByMatchId"][match_id] = hashlib.sha256(
        f"CC-316-partition-v1|{match_id}".encode()
    ).hexdigest()
    discovery["candidateProvenanceByMatchId"][match_id] = {
        "depth": 1,
        "source": "PARTICIPANT_MATCH_LIST",
    }
    discovery["uniqueCandidateCount"] += 1
    discovery["metadataNotAttemptedMatchIds"] = [match_id]
    discovery["metadataUnknownReasonByMatchId"] = {match_id: "MAX_CANDIDATES"}
    discovery["stopReason"] = "MAX_CANDIDATES"
    partition["exclusionCounts"] = {"NOT_FETCHED_MAX_CANDIDATES": 1}
    partition["explorationMatchIds"] = []
    partition["confirmationPotentialMatchIds"] = []
    partition["overlapExcludedMatchIds"] = []
    partition["usable"] = False
    artifact["status"] = "NO_SAMPLE"
    return artifact


def _synthetic_snapshot_with_candidate_cap_tail() -> dict[str, object]:
    artifact = _synthetic_snapshot_contract()
    candidates = sorted(f"BR1_{index}" for index in range(1, 323))
    digests = {
        match_id: hashlib.sha256(f"CC-316-partition-v1|{match_id}".encode()).hexdigest()
        for match_id in candidates
    }
    discovery = artifact["discovery"]
    partition = artifact["partition"]
    assert isinstance(discovery, dict) and isinstance(partition, dict)
    attempted, unknown = candidates[:320], candidates[320:]
    discovery.update(
        {
            "uniqueCandidateCount": len(candidates),
            "metadataRequestAttemptCount": len(attempted),
            "structuralMetadataValidCount": len(attempted),
            "stopReason": "MAX_CANDIDATES",
            "candidateProvenanceByMatchId": {
                match_id: {"depth": 0, "source": "CONSENTED_SEED_MATCH_LIST"}
                for match_id in candidates
            },
            "metadataRequestAttemptedMatchIds": attempted,
            "structuralMetadataValidMatchIds": attempted,
            "metadataNotAttemptedMatchIds": unknown,
            "metadataUnknownReasonByMatchId": {match_id: "MAX_CANDIDATES" for match_id in unknown},
        }
    )
    partition.update(
        {
            "partitionDigestByMatchId": digests,
            "candidateMatchIds": candidates,
            "explorationMatchIds": [],
            "confirmationPotentialMatchIds": [],
            "overlapExcludedMatchIds": [],
            "usable": False,
            "exclusionCounts": {"NOT_FETCHED_MAX_CANDIDATES": 2},
        }
    )
    artifact["status"] = "NO_SAMPLE"
    return artifact


@pytest.mark.parametrize(
    "corrupt",
    [
        "duplicate_candidate",
        "missing_provenance",
        "changed_digest",
        "attempted_missing_candidate",
        "valid_not_attempted",
        "unknown_reason_not_allowlisted",
        "invalid_source_depth",
        "overlapping_partitions",
        "wrong_exploration_order",
        "privacy_flag_changed",
        "seed_source_nonzero_depth",
        "complete_universe_per_player_stop",
    ],
)
def test_snapshot_contract_rejects_synthetic_corruption_before_write(
    tmp_path: Path, corrupt: str
) -> None:
    artifact = _synthetic_snapshot_contract()
    discovery = artifact["discovery"]
    partition = artifact["partition"]
    assert isinstance(discovery, dict) and isinstance(partition, dict)
    candidates = partition["candidateMatchIds"]
    assert isinstance(candidates, list)
    if corrupt == "duplicate_candidate":
        candidates.append(candidates[0])
    elif corrupt == "missing_provenance":
        discovery["candidateProvenanceByMatchId"].pop(candidates[0])
    elif corrupt == "changed_digest":
        partition["partitionDigestByMatchId"][candidates[0]] = "0" * 64
    elif corrupt == "attempted_missing_candidate":
        discovery["metadataRequestAttemptedMatchIds"].pop()
    elif corrupt == "valid_not_attempted":
        incomplete = _synthetic_incomplete_snapshot_contract()
        incomplete_discovery = incomplete["discovery"]
        incomplete_partition = incomplete["partition"]
        unattempted_id = "BR1_1"
        incomplete_discovery["metadataRequestAttemptedMatchIds"].remove(unattempted_id)
        incomplete_discovery["metadataNotAttemptedMatchIds"].append(unattempted_id)
        incomplete_discovery["metadataNotAttemptedMatchIds"].sort()
        incomplete_discovery["metadataUnknownReasonByMatchId"][unattempted_id] = "MAX_CANDIDATES"
        incomplete_discovery["metadataRequestAttemptCount"] -= 1
        incomplete_partition["exclusionCounts"]["NOT_FETCHED_MAX_CANDIDATES"] = 2
        artifact = incomplete
    elif corrupt == "unknown_reason_not_allowlisted":
        incomplete = _synthetic_incomplete_snapshot_contract()
        incomplete["discovery"]["metadataUnknownReasonByMatchId"]["BR1_21"] = "PRIVATE_DETAIL"
        artifact = incomplete
    elif corrupt == "invalid_source_depth":
        discovery["candidateProvenanceByMatchId"][candidates[0]] = {
            "depth": 0,
            "source": "PARTICIPANT_MATCH_LIST",
        }
    elif corrupt == "overlapping_partitions":
        partition["confirmationPotentialMatchIds"] = [partition["explorationMatchIds"][0]]
    elif corrupt == "wrong_exploration_order":
        partition["explorationMatchIds"].reverse()
    elif corrupt == "seed_source_nonzero_depth":
        discovery["candidateProvenanceByMatchId"][candidates[0]] = {
            "depth": 2,
            "source": "CONSENTED_SEED_MATCH_LIST",
        }
    elif corrupt == "complete_universe_per_player_stop":
        discovery["stopReason"] = "MAX_MATCHES_PER_PLAYER"
    else:
        artifact["privacy"]["timelineFetched"] = True

    snapshot_path = tmp_path / ".private" / "synthetic-snapshot.json"
    snapshot_path.parent.mkdir(mode=0o700)
    with pytest.raises(DiscoveryError, match="contrato do snapshot inválido"):
        persist_snapshot_once(snapshot_path, artifact)
    assert not snapshot_path.exists()
    assert not snapshot_path.with_suffix(".json.sha256").exists()


def test_snapshot_contract_accepts_complete_and_incomplete_synthetic_examples() -> None:
    validate_snapshot_contract(_synthetic_snapshot_contract())
    validate_snapshot_contract(_synthetic_incomplete_snapshot_contract())
    stopped_before_cap = _synthetic_snapshot_contract()
    stopped_discovery = stopped_before_cap["discovery"]
    assert isinstance(stopped_discovery, dict)
    stopped_discovery["stopReason"] = "MAX_CANDIDATES"
    validate_snapshot_contract(stopped_before_cap)


def test_snapshot_contract_preserves_candidate_tail_above_metadata_cap() -> None:
    artifact = _synthetic_snapshot_with_candidate_cap_tail()
    validate_snapshot_contract(artifact)
    partition = artifact["partition"]
    assert isinstance(partition, dict)
    assert len(partition["candidateMatchIds"]) == 322
    assert (
        partition["candidateMatchIds"][-2:] == artifact["discovery"]["metadataNotAttemptedMatchIds"]
    )


def test_operational_temporal_baseline_is_private_verified_and_immutable(
    tmp_path: Path,
) -> None:
    path = _baseline_test_path(tmp_path)
    assert (
        temporal_budget_baseline_path()
        .as_posix()
        .endswith("22-temporal-budget-baseline-option-b-2026-10-02.json")
    )
    digest = create_operational_budget_baseline(path)
    sidecar = path.with_suffix(path.suffix + ".sha256")

    assert load_operational_budget_baseline(path) == (546, 1_671_911_000_000)
    assert path.stat().st_mode & 0o777 == 0o600
    assert sidecar.stat().st_mode & 0o777 == 0o600
    assert sidecar.read_text(encoding="ascii") == f"{digest}  {path.name}\n"
    with pytest.raises(DiscoveryError, match="imutável não será sobrescrito"):
        create_operational_budget_baseline(path)


@pytest.mark.parametrize(
    "corruption",
    [
        "missing_sidecar",
        "hash_divergence",
        "invalid_schema",
        "excessive_time",
        "excessive_requests",
        "duplicate_covered_attempt",
        "wrong_amendment_hash",
        "insecure_sidecar",
    ],
)
def test_temporal_baseline_validation_fails_closed(tmp_path: Path, corruption: str) -> None:
    path = _baseline_test_path(tmp_path)
    create_operational_budget_baseline(path)
    sidecar = path.with_suffix(path.suffix + ".sha256")
    if corruption == "missing_sidecar":
        sidecar.unlink()
    elif corruption == "hash_divergence":
        path.write_bytes(path.read_bytes() + b" ")
    elif corruption == "insecure_sidecar":
        sidecar.chmod(0o644)
    else:
        state = json.loads(path.read_text(encoding="utf-8"))
        if corruption == "invalid_schema":
            state["schemaVersion"] = 99
        elif corruption == "excessive_time":
            state["operationalElapsedSeconds"] = 3_600.001
        elif corruption == "excessive_requests":
            state["reservedRequests"] = 1_001
        elif corruption == "duplicate_covered_attempt":
            state["coveredAttempts"] = [1, 2, 2, 4, 5, 6]
        else:
            state["amendmentSha256"] = "0" * 64
        _rewrite_baseline_fixture(path, state)

    with pytest.raises(DiscoveryError, match="baseline temporal"):
        load_operational_budget_baseline(path)


def test_temporal_baseline_attempt_seven_skips_legacy_ledgers(tmp_path: Path) -> None:
    private = tmp_path / ".private" / "cc316"
    private.mkdir(parents=True, mode=0o700)
    private.chmod(0o700)
    baseline = _baseline_test_path(tmp_path)
    create_operational_budget_baseline(baseline)
    for attempt in range(1, 7):
        legacy_path = private / (
            "request-budget.json" if attempt == 1 else f"request-budget-{attempt:02d}.json"
        )
        legacy_path.write_text("legacy must not be read", encoding="utf-8")

    totals = validate_prior_budget_history(
        private / "request-budget-07.json",
        7,
        boot_identifier=current_boot_id(),
        current_process_started_monotonic_ns=time.monotonic_ns(),
        temporal_baseline_path=baseline,
    )
    assert totals == (546, 1_671_911_000_000)
    assert all(
        (private / ("request-budget.json" if n == 1 else f"request-budget-{n:02d}.json")).read_text(
            encoding="utf-8"
        )
        == "legacy must not be read"
        for n in range(1, 7)
    )


def test_new_lifecycle_adds_once_after_approved_baseline(tmp_path: Path) -> None:
    private = tmp_path / ".private" / "cc316"
    private.mkdir(parents=True, mode=0o700)
    private.chmod(0o700)
    baseline = _baseline_test_path(tmp_path)
    create_operational_budget_baseline(baseline)
    boot_id = current_boot_id()
    started = time.monotonic_ns() - 2_000_000_000
    finished = started + 1_000_000_000
    (private / ".cc316-discovery-consumed-07").write_text("synthetic", encoding="utf-8")
    (private / "request-budget-07.json").write_text(
        json.dumps({"schemaVersion": 3, "status": "COMPLETED", "requestsReserved": 7}),
        encoding="utf-8",
    )
    process_lifecycle_path(private / "request-budget-08.json", 7).write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "attemptNumber": 7,
                "lifecycleStatus": "COMPLETE",
                "runOutcome": "COMPLETED",
                "bootId": boot_id,
                "processStartedMonotonicNs": started,
                "coroutineStartedMonotonicNs": started + 100_000_000,
                "coroutineFinishedMonotonicNs": started + 900_000_000,
                "processFinishedMonotonicNs": finished,
                "processElapsedNs": finished - started,
                "shutdownElapsedNs": 100_000_000,
                "executorShutdownTimeoutNs": 300_000_000_000,
            }
        ),
        encoding="utf-8",
    )

    totals = validate_prior_budget_history(
        private / "request-budget-08.json",
        8,
        boot_identifier=boot_id,
        current_process_started_monotonic_ns=time.monotonic_ns(),
        temporal_baseline_path=baseline,
    )
    assert totals == (553, 1_672_911_000_000)


@pytest.mark.asyncio
async def test_unknown_candidate_budget_blocks_before_any_io(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot, journal = _private_paths(tmp_path)
    baseline = _baseline_test_path(tmp_path)
    create_operational_budget_baseline(baseline)
    monkeypatch.setattr(
        discovery_module,
        "require_candidate_budget_resolved",
        require_candidate_budget_resolved,
    )
    transport = FakeTransport({}, {})

    with pytest.raises(DiscoveryError, match="descoberta CC-316 encerrada pela emenda CC-316-B"):
        await run_cc316_discovery(
            transport=transport,
            seeds=[_seed()],
            subject_hash_secret="fake-secret",
            artifact_path=snapshot,
            budget_journal_path=journal,
            attempt_number=7,
            temporal_baseline_path=baseline,
        )

    assert transport.requests_made == 0
    assert not snapshot.exists()
    assert not journal.exists()
    assert not (snapshot.parent / ".cc316-discovery-consumed-07").exists()


def test_cli_candidate_gate_runs_before_settings_seeds_or_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output_dir = tmp_path / ".private" / "cc316"
    monkeypatch.setattr(
        discovery_module,
        "require_candidate_budget_resolved",
        require_candidate_budget_resolved,
    )
    with pytest.raises(typer.Exit):
        collect_structural_cc316(
            seeds_file=tmp_path / "missing-seeds.yaml",
            output_dir=output_dir,
            attempt=7,
            env_file=None,
        )
    assert not output_dir.exists()


@pytest.mark.parametrize("baseline_state", ["missing", "tampered"])
@pytest.mark.asyncio
async def test_invalid_temporal_baseline_blocks_before_any_io(
    tmp_path: Path, baseline_state: str
) -> None:
    snapshot, journal = _private_paths(tmp_path)
    baseline = _baseline_test_path(tmp_path)
    if baseline_state == "tampered":
        create_operational_budget_baseline(baseline)
        baseline.write_bytes(baseline.read_bytes() + b"tampered")
    transport = FakeTransport({}, {})

    with pytest.raises(DiscoveryError, match="baseline temporal"):
        await run_cc316_discovery(
            transport=transport,
            seeds=[_seed()],
            subject_hash_secret="fake-secret",
            artifact_path=snapshot,
            budget_journal_path=journal,
            attempt_number=7,
            temporal_baseline_path=baseline,
        )

    assert transport.requests_made == 0
    assert not snapshot.exists()
    assert not journal.exists()
    assert not (snapshot.parent / ".cc316-discovery-consumed-07").exists()


@pytest.mark.asyncio
async def test_discovery_respects_participant_hops_dedupe_and_private_snapshot(
    tmp_path: Path,
) -> None:
    snapshot, journal = _private_paths(tmp_path)
    ids = [f"BR1_{index}" for index in range(1, 7)]
    transport = FakeTransport(
        players_to_matches={
            "seed-player": ids[:4] + ids[:2],
            "participant-depth-1": ids[2:6],
            "participant-depth-2": ["BR1_999"],
        },
        bodies={
            **{match_id: _body(match_id, ["participant-depth-1"]) for match_id in ids},
            "BR1_999": _body("BR1_999", ["beyond-depth"]),
        },
    )

    result = await run_cc316_discovery(
        transport=transport,
        seeds=[_seed()],
        subject_hash_secret="fake-secret",
        artifact_path=snapshot,
        budget_journal_path=journal,
    )

    assert result.summary.candidates_seen == 6
    assert set(transport.match_calls or []) == set(ids)
    assert all(call[1:] == (420, 0, 4) for call in transport.list_calls or [])
    artifact = json.loads(snapshot.read_text())
    assert artifact["status"] == "NO_SAMPLE"
    assert artifact["privacy"]["matchBodiesPersisted"] is False
    assert "participant-depth-1" not in snapshot.read_text()
    assert "participant-depth-2" not in snapshot.read_text()
    assert "HMAC" not in snapshot.read_text()
    assert snapshot.stat().st_mode & 0o777 == 0o600
    assert journal.exists()
    assert not any(path.name.endswith(".raw") for path in snapshot.parent.iterdir())


@pytest.mark.asyncio
async def test_auth_failure_stops_after_first_reserved_request(tmp_path: Path) -> None:
    snapshot, journal = _private_paths(tmp_path)
    transport = FakeTransport({}, {}, fail_kind="auth")

    with pytest.raises(SourceAuthError):
        await run_cc316_discovery(
            transport=transport,
            seeds=[_seed()],
            subject_hash_secret="fake-secret",
            artifact_path=snapshot,
            budget_journal_path=journal,
        )

    assert transport.requests_made == 1
    assert json.loads(journal.read_text())["status"] == "STOPPED_AUTH"
    assert not snapshot.exists()


@pytest.mark.asyncio
async def test_rate_limit_stops_without_following_participant_calls(tmp_path: Path) -> None:
    snapshot, journal = _private_paths(tmp_path)
    transport = FakeTransport({}, {}, fail_kind="rate")

    with pytest.raises(SourceRateLimitedError):
        await run_cc316_discovery(
            transport=transport,
            seeds=[_seed()],
            subject_hash_secret="fake-secret",
            artifact_path=snapshot,
            budget_journal_path=journal,
        )

    assert transport.requests_made == 2
    assert json.loads(journal.read_text())["requestsReserved"] == 2
    assert json.loads(journal.read_text())["status"] == "STOPPED_RATE_OR_BUDGET"


@pytest.mark.asyncio
async def test_missing_match_is_recorded_as_exclusion_and_no_sample(tmp_path: Path) -> None:
    snapshot, journal = _private_paths(tmp_path)
    transport = FakeTransport(
        players_to_matches={"seed-player": ["BR1_123"]},
        bodies={},
        fail_kind="missing-match",
    )

    result = await run_cc316_discovery(
        transport=transport,
        seeds=[_seed()],
        subject_hash_secret="fake-secret",
        artifact_path=snapshot,
        budget_journal_path=journal,
    )

    artifact = json.loads(snapshot.read_text())
    assert result.summary.status == "NO_SAMPLE"
    assert artifact["partition"]["structuralExcludedMatchIds"] == {"BR1_123": "MATCH_NOT_FOUND"}


@pytest.mark.asyncio
async def test_second_invocation_is_blocked_before_any_request(tmp_path: Path) -> None:
    snapshot, journal = _private_paths(tmp_path)
    first = FakeTransport({}, {})
    await run_cc316_discovery(
        transport=first,
        seeds=[_seed()],
        subject_hash_secret="fake-secret",
        artifact_path=snapshot,
        budget_journal_path=journal,
    )
    second = FakeTransport({}, {})

    with pytest.raises(DiscoveryError):
        await run_cc316_discovery(
            transport=second,
            seeds=[_seed()],
            subject_hash_secret="fake-secret",
            artifact_path=snapshot.with_name("another-snapshot.json"),
            budget_journal_path=journal.with_name("another-budget.json"),
        )

    assert second.requests_made == 0


@pytest.mark.asyncio
async def test_candidate_cap_stops_listing_before_overflow_and_keeps_universe_complete(
    tmp_path: Path,
) -> None:
    snapshot, journal = _private_paths(tmp_path)
    root_ids = [f"BR1_{index}" for index in range(1, 5)]
    participant_ids = [f"participant-{index}" for index in range(80)]
    players_to_matches = {"seed-player": root_ids}
    bodies = {
        match_id: _body(match_id, participant_ids[index * 20 : (index + 1) * 20])
        for index, match_id in enumerate(root_ids)
    }
    for player_index, player_id in enumerate(participant_ids):
        match_ids = [f"BR1_{1000 + player_index * 4 + offset}" for offset in range(4)]
        players_to_matches[player_id] = match_ids
        bodies.update({match_id: _body(match_id, ["next-hop"]) for match_id in match_ids})
    transport = FakeTransport(
        players_to_matches=players_to_matches, bodies=bodies, honor_count=True
    )

    result = await run_cc316_discovery(
        transport=transport,
        seeds=[_seed()],
        subject_hash_secret="fake-secret",
        artifact_path=snapshot,
        budget_journal_path=journal,
    )

    artifact = json.loads(snapshot.read_text())
    discovery = artifact["discovery"]
    # Four seed IDs plus 79 full listings fill the 320 slots; the last player is never listed.
    assert [call[0] for call in transport.list_calls or []] == [
        "seed-player",
        *participant_ids[:79],
    ]
    assert all(call[1:] == (420, 0, 4) for call in transport.list_calls or [])
    assert discovery["uniqueCandidateCount"] == 320
    assert discovery["metadataRequestAttemptCount"] == 320
    assert discovery["metadataNotAttemptedMatchIds"] == []
    assert discovery["stopReason"] == "MAX_CANDIDATES"
    assert len(transport.match_calls or []) == 320
    assert set(players_to_matches[participant_ids[79]]).isdisjoint(
        artifact["partition"]["candidateMatchIds"]
    )
    assert result.summary.status == "PARTITION_POTENTIAL_READY"
    assert artifact["partition"]["usable"] is True
    assert result.partition.status == "READY"
    assert len(result.partition.exploration_ids) == 20
    assert list(result.partition.exploration_ids) == artifact["partition"]["explorationMatchIds"]
    assert result.summary.exploration_count == 20
    provenance = discovery["candidateProvenanceByMatchId"]
    assert all(
        provenance[match_id] == {"depth": 0, "source": "CONSENTED_SEED_MATCH_LIST"}
        for match_id in root_ids
    )
    saved = snapshot.read_text()
    assert "participant-0" not in saved
    assert "next-hop" not in saved
    assert "fake-secret" not in saved


@pytest.mark.asyncio
async def test_rediscovered_seed_match_keeps_seed_provenance_under_cap(tmp_path: Path) -> None:
    snapshot, journal = _private_paths(tmp_path)
    root_ids = ["BR1_1", "BR1_2"]
    participants = [f"participant-{index}" for index in range(80)]
    players_to_matches = {"seed-player": root_ids}
    bodies = {match_id: _body(match_id, participants) for match_id in root_ids}
    for index, player in enumerate(participants):
        if index == 0:
            ids = [root_ids[0], *[f"BR1_{100 + n}" for n in range(3)]]
        else:
            start = 103 + (index - 1) * 4
            ids = [f"BR1_{start + n}" for n in range(4)]
        players_to_matches[player] = ids
        bodies.update({match_id: _body(match_id, ["deep-player"]) for match_id in ids})
    players_to_matches["deep-player"] = []
    transport = FakeTransport(players_to_matches, bodies, honor_count=True)

    result = await run_cc316_discovery(
        transport=transport,
        seeds=[_seed()],
        subject_hash_secret="fake-secret",
        artifact_path=snapshot,
        budget_journal_path=journal,
    )

    discovery = json.loads(snapshot.read_text())["discovery"]
    # 2 seed IDs + 3 fresh from player 0 + 78 full listings = 317; player 79 would overflow.
    assert discovery["uniqueCandidateCount"] == 317
    assert discovery["metadataNotAttemptedMatchIds"] == []
    assert discovery["stopReason"] == "MAX_CANDIDATES"
    assert discovery["candidateProvenanceByMatchId"][root_ids[0]] == {
        "depth": 0,
        "source": "CONSENTED_SEED_MATCH_LIST",
    }
    assert participants[79] not in [call[0] for call in transport.list_calls or []]
    assert result.summary.status == "PARTITION_POTENTIAL_READY"


@pytest.mark.asyncio
async def test_listing_overflow_at_cap_freezes_unknown_tail_and_fails_closed(
    tmp_path: Path,
) -> None:
    snapshot, journal = _private_paths(tmp_path)
    root_ids = [f"BR1_{index}" for index in range(1, 5)]
    participant_ids = [f"participant-{index}" for index in range(79)]
    players_to_matches = {"seed-player": root_ids}
    bodies = {
        match_id: _body(match_id, participant_ids[index * 20 : (index + 1) * 20])
        for index, match_id in enumerate(root_ids)
    }
    for player_index, player_id in enumerate(participant_ids):
        # The last listing ignores `count` and returns two IDs past the free slots.
        width = 6 if player_index == 78 else 4
        match_ids = [f"BR1_{1000 + player_index * 10 + offset}" for offset in range(width)]
        players_to_matches[player_id] = match_ids
        bodies.update({match_id: _body(match_id, ["next-hop"]) for match_id in match_ids})
    tail_ids = sorted(players_to_matches[participant_ids[78]][4:])
    transport = FakeTransport(players_to_matches=players_to_matches, bodies=bodies)

    result = await run_cc316_discovery(
        transport=transport,
        seeds=[_seed()],
        subject_hash_secret="fake-secret",
        artifact_path=snapshot,
        budget_journal_path=journal,
    )

    artifact = json.loads(snapshot.read_text())
    discovery = artifact["discovery"]
    assert discovery["uniqueCandidateCount"] == 322
    assert discovery["metadataRequestAttemptCount"] == 320
    assert discovery["metadataNotAttemptedMatchIds"] == tail_ids
    assert discovery["metadataUnknownReasonByMatchId"] == {
        match_id: "MAX_CANDIDATES" for match_id in tail_ids
    }
    assert discovery["stopReason"] == "MAX_CANDIDATES"
    assert artifact["partition"]["exclusionCounts"]["NOT_FETCHED_MAX_CANDIDATES"] == 2
    assert len(transport.match_calls or []) == 320
    assert result.summary.status == "NO_SAMPLE"
    assert result.summary.exploration_count == 0
    assert result.partition.status == "NO_SAMPLE"
    assert result.partition.exploration_ids == ()
    assert artifact["partition"]["usable"] is False
    assert artifact["partition"]["explorationMatchIds"] == []
    assert set(artifact["partition"]["structuralExcludedMatchIds"]).isdisjoint(tail_ids)


@pytest.mark.asyncio
async def test_retry_counts_only_complete_same_boot_process_history(tmp_path: Path) -> None:
    snapshot, journal = _private_paths(tmp_path)
    private = snapshot.parent
    boot_id = current_boot_id()
    history_base_ns = time.monotonic_ns() - 600_000_000_000
    for attempt, reserved, status in (
        (1, 5, "INTERRUPTED"),
        (2, 5, "INTERRUPTED"),
        (3, 109, "INTERRUPTED"),
        (4, 417, "COMPLETED"),
    ):
        marker = private / (
            ".cc316-discovery-consumed"
            if attempt == 1
            else f".cc316-discovery-consumed-{attempt:02d}"
        )
        marker.write_text("previous attempt")
        previous_journal = private / (
            "request-budget.json" if attempt == 1 else f"request-budget-{attempt:02d}.json"
        )
        previous_journal.write_text(
            json.dumps(
                {
                    "schemaVersion": 3,
                    "status": status,
                    "requestsReserved": reserved,
                    "startedAt": "legacy-wall-time-is-not-used",
                    "finishedAt": "legacy-wall-time-is-not-used",
                }
            )
        )
        process_start = history_base_ns + (attempt - 1) * 100_000_000_000
        process_end = process_start + 100_000_000_000
        process_lifecycle_path(journal, attempt).write_text(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "attemptNumber": attempt,
                    "lifecycleStatus": "COMPLETE",
                    "runOutcome": "COMPLETED" if status == "COMPLETED" else "INTERRUPTED",
                    "bootId": boot_id,
                    "processStartedMonotonicNs": process_start,
                    "coroutineStartedMonotonicNs": process_start + 1,
                    "coroutineFinishedMonotonicNs": process_start + 50_000_000_000,
                    "processFinishedMonotonicNs": process_end,
                    "processElapsedNs": process_end - process_start,
                    "shutdownElapsedNs": process_end - process_start - 50_000_000_000,
                    "executorShutdownTimeoutNs": 300_000_000_000,
                }
            )
        )

    transport = FakeTransport({}, {})
    await run_cc316_discovery(
        transport=transport,
        seeds=[_seed()],
        subject_hash_secret="fake-secret",
        artifact_path=snapshot,
        budget_journal_path=private / "request-budget-05.json",
        attempt_number=5,
    )

    assert transport.request_limit == 464
    assert 2_800 < transport.time_limit_seconds <= 2_900
    assert json.loads((private / "request-budget-05.json").read_text())["requestsReserved"] == 2


@pytest.mark.parametrize("failure", ["legacy", "boot", "missing-end"])
@pytest.mark.asyncio
async def test_legacy_incomplete_or_incomparable_history_fails_closed(
    tmp_path: Path, failure: str
) -> None:
    snapshot, journal = _private_paths(tmp_path)
    private = snapshot.parent
    boot_id = current_boot_id()
    history_base_ns = time.monotonic_ns() - 200_000_000_000
    marker = private / ".cc316-discovery-consumed"
    marker.write_text("previous attempt")
    (private / "request-budget.json").write_text(
        json.dumps(
            {
                "schemaVersion": 2 if failure == "legacy" else 3,
                "status": "INTERRUPTED",
                "requestsReserved": 5,
            }
        )
    )
    lifecycle = {
        "schemaVersion": 1,
        "attemptNumber": 1,
        "lifecycleStatus": "COMPLETE",
        "runOutcome": "INTERRUPTED",
        "bootId": "00000000-0000-0000-0000-000000000001" if failure == "boot" else boot_id,
        "processStartedMonotonicNs": history_base_ns,
        "coroutineStartedMonotonicNs": history_base_ns + 1,
        "coroutineFinishedMonotonicNs": history_base_ns + 50,
        "processFinishedMonotonicNs": history_base_ns + 100,
        "processElapsedNs": 100,
        "shutdownElapsedNs": 50,
        "executorShutdownTimeoutNs": 300_000_000_000,
    }
    if failure == "missing-end":
        lifecycle.pop("processFinishedMonotonicNs")
    process_lifecycle_path(journal, 1).write_text(json.dumps(lifecycle))
    transport = FakeTransport({}, {})

    with pytest.raises(DiscoveryError, match="decisão/emenda explícita"):
        await run_cc316_discovery(
            transport=transport,
            seeds=[_seed()],
            subject_hash_secret="fake-secret",
            artifact_path=snapshot,
            budget_journal_path=private / "request-budget-02.json",
            attempt_number=2,
        )

    assert transport.requests_made == 0
    assert transport.request_limit == 1_000
    assert not snapshot.exists()
    assert not (private / ".cc316-discovery-consumed-02").exists()
    assert not (private / "request-budget-02.json").exists()


def test_history_after_current_process_start_fails_closed(tmp_path: Path) -> None:
    snapshot, journal = _private_paths(tmp_path)
    private = snapshot.parent
    boot_id = current_boot_id()
    started = time.monotonic_ns() - 100
    marker = private / ".cc316-discovery-consumed"
    marker.write_text("previous attempt")
    (private / "request-budget.json").write_text(
        json.dumps({"schemaVersion": 3, "status": "INTERRUPTED", "requestsReserved": 5})
    )
    process_lifecycle_path(journal, 1).write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "attemptNumber": 1,
                "lifecycleStatus": "COMPLETE",
                "runOutcome": "INTERRUPTED",
                "bootId": boot_id,
                "processStartedMonotonicNs": started - 10,
                "coroutineStartedMonotonicNs": started - 9,
                "coroutineFinishedMonotonicNs": started + 1,
                "processFinishedMonotonicNs": started + 2,
                "processElapsedNs": 12,
                "shutdownElapsedNs": 1,
                "executorShutdownTimeoutNs": 300_000_000_000,
            }
        )
    )

    with pytest.raises(DiscoveryError, match="monotônico incoerente"):
        validate_prior_budget_history(
            journal,
            2,
            boot_identifier=boot_id,
            current_process_started_monotonic_ns=started,
        )


@pytest.mark.asyncio
async def test_ids_beyond_per_player_metadata_limit_are_frozen_as_unknown(
    tmp_path: Path,
) -> None:
    snapshot, journal = _private_paths(tmp_path)
    returned_ids = [f"BR1_{index}" for index in range(201, 207)]
    transport = FakeTransport(
        players_to_matches={"seed-player": returned_ids, "participant": []},
        bodies={match_id: _body(match_id, ["participant"]) for match_id in returned_ids[:4]},
    )

    result = await run_cc316_discovery(
        transport=transport,
        seeds=[_seed()],
        subject_hash_secret="fake-secret",
        artifact_path=snapshot,
        budget_journal_path=journal,
    )

    artifact = json.loads(snapshot.read_text())
    assert result.summary.candidates_seen == 6
    assert artifact["discovery"]["metadataRequestAttemptCount"] == 4
    assert artifact["discovery"]["metadataNotAttemptedMatchIds"] == returned_ids[4:]
    assert artifact["discovery"]["metadataUnknownReasonByMatchId"] == {
        match_id: "MAX_MATCHES_PER_PLAYER" for match_id in returned_ids[4:]
    }
    assert set(artifact["discovery"]["candidateProvenanceByMatchId"]) == set(returned_ids)
    assert artifact["partition"]["usable"] is False
    assert artifact["partition"]["explorationMatchIds"] == []


def test_budget_journal_reservation_failure_prevents_request(tmp_path: Path) -> None:
    private = tmp_path / ".private"
    private.mkdir(mode=0o700)
    journal_path = private / "budget.json"
    with BudgetJournal(journal_path, limit=1) as journal:
        journal.reserve()
        with pytest.raises(DiscoveryError):
            journal.reserve()
        journal.finish(status="INTERRUPTED", failure_code="UPSTREAM_UNAVAILABLE")
    saved = json.loads(journal_path.read_text())
    assert saved["requestsReserved"] == 1
    assert saved["failureCode"] == "UPSTREAM_UNAVAILABLE"


@pytest.mark.parametrize(
    ("ticks", "expected_status", "expected_shutdown_ns"),
    [
        ([100, 120, 140, 170], "COMPLETE", 30),
        (
            [100, 120, 140, 140 + 300_000_000_000],
            "SHUTDOWN_TIMEOUT",
            300_000_000_000,
        ),
    ],
)
def test_process_lifecycle_journal_records_full_monotonic_shutdown(
    tmp_path: Path,
    ticks: list[int],
    expected_status: str,
    expected_shutdown_ns: int,
) -> None:
    private = tmp_path / ".private"
    private.mkdir(mode=0o700)
    tick_iter = iter(ticks)
    path = private / "process-lifecycle.json"
    lifecycle = ProcessLifecycleJournal(
        path, boot_id="00000000-0000-0000-0000-000000000001", clock_ns=lambda: next(tick_iter)
    )

    lifecycle.__enter__()
    lifecycle.mark_coroutine_started()
    lifecycle.mark_coroutine_finished()
    lifecycle.finish(run_outcome="INTERRUPTED")

    saved = json.loads(path.read_text())
    assert saved["processStartedMonotonicNs"] == ticks[0]
    assert saved["coroutineStartedMonotonicNs"] == ticks[1]
    assert saved["coroutineFinishedMonotonicNs"] == ticks[2]
    assert saved["processFinishedMonotonicNs"] == ticks[3]
    assert saved["processElapsedNs"] == ticks[3] - ticks[0]
    assert saved["shutdownElapsedNs"] == expected_shutdown_ns
    assert saved["executorShutdownTimeoutNs"] == 300_000_000_000
    assert saved["lifecycleStatus"] == expected_status
    assert saved["runOutcome"] == "INTERRUPTED"
    assert path.stat().st_mode & 0o777 == 0o600


def test_process_lifecycle_preserves_cli_start_before_preflight(tmp_path: Path) -> None:
    private = tmp_path / ".private"
    private.mkdir(mode=0o700)
    tick_iter = iter([120, 140, 170])
    path = private / "process-lifecycle.json"
    lifecycle = ProcessLifecycleJournal(
        path,
        boot_id="00000000-0000-0000-0000-000000000001",
        process_started_monotonic_ns=100,
        clock_ns=lambda: next(tick_iter),
    )

    lifecycle.__enter__()
    lifecycle.mark_coroutine_started()
    lifecycle.mark_coroutine_finished()
    lifecycle.finish(run_outcome="COMPLETED")

    saved = json.loads(path.read_text())
    assert saved["processStartedMonotonicNs"] == 100
    assert saved["coroutineStartedMonotonicNs"] == 120
    assert saved["processElapsedNs"] == 70


def test_budget_journal_rejects_unallowlisted_request_class(tmp_path: Path) -> None:
    private = tmp_path / ".private"
    private.mkdir(mode=0o700)
    journal_path = private / "budget.json"
    with BudgetJournal(journal_path, limit=1) as journal:
        with pytest.raises(DiscoveryError):
            journal.reserve("/lol/match/v5/matches/PRIVATE_ID")
    saved = json.loads(journal_path.read_text())
    assert saved["requestsReserved"] == 0
    assert saved["diagnostics"] == []


def test_budget_journal_records_allowlisted_local_budget_without_request(
    tmp_path: Path,
) -> None:
    private = tmp_path / ".private"
    private.mkdir(mode=0o700)
    journal_path = private / "budget.json"
    with BudgetJournal(journal_path, limit=1) as journal:
        journal.record_local_budget("match-list", "LOCAL_TIME_BUDGET")
    saved = json.loads(journal_path.read_text())
    assert saved["requestsReserved"] == 0
    assert saved["diagnostics"][0]["ordinal"] is None
    assert saved["diagnostics"][0]["requestClass"] == "match-list"
    assert saved["diagnostics"][0]["result"] == "LOCAL_TIME_BUDGET"
