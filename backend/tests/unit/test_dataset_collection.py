"""CC-323 dataset collection: authorization, campaign ledger, snowball runner and CLI.

Every fixture is synthetic: no test opens the network, the real private
collection directory or a database.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import typer

import riftlab.collection.cc316_exploration as exploration_module
import riftlab.collection.dataset_collection as dataset
from riftlab.cli.collect import collect_dataset_16_19
from riftlab.collection.cc316_discovery import DiscoveryError
from riftlab.collection.seeds import Seed
from riftlab.ingestion.infrastructure.raw_store import DurableRawMatchStore
from riftlab.ingestion.infrastructure.riot_account import RiotAccountConfig
from riftlab.ingestion.infrastructure.riot_transport import RawRiotResponse
from riftlab.shared.errors import (
    SourceAuthError,
    SourceError,
    SourceNotFoundError,
    SourceRateLimitedError,
)
from support.cc316_partition import private_dir, write_private_with_sidecar

SEEDS_BYTES = b"schemaVersion: v1\nseeds: []\n"
SEED = Seed(
    label="seed-01",
    account=RiotAccountConfig(
        game_name="FakeSeed", tag_line="TEST", platform="BR1", regional_routing="AMERICAS"
    ),
)
KAISA, CAITLYN, EZREAL, OTHER = 145, 51, 81, 1


@dataclass
class World:
    """Synthetic Riot data: listings per PUUID and match specs keyed by match ID."""

    listings: dict[str, list[str]] = field(default_factory=dict)
    matches: dict[str, dict[str, Any]] = field(default_factory=dict)
    accounts: dict[tuple[str, str], str] = field(
        default_factory=lambda: {("FakeSeed", "TEST"): "puuid-seed"}
    )

    def match(
        self,
        match_id: str,
        participants: list[tuple[str, int, str]],
        *,
        version: str = "16.19.715.1234",
        result: str = "GameComplete",
        identity: str | None = None,
    ) -> None:
        self.matches[match_id] = {
            "participants": participants,
            "version": version,
            "result": result,
            "identity": identity or match_id,
        }


def _match_body(match_id: str, spec: dict[str, Any]) -> bytes:
    return json.dumps(
        {
            "metadata": {
                "matchId": spec["identity"],
                "participants": [puuid for puuid, _, _ in spec["participants"]],
            },
            "info": {
                "gameVersion": spec["version"],
                "queueId": 420,
                "mapId": 11,
                "endOfGameResult": spec["result"],
                "participants": [
                    {
                        "participantId": index + 1,
                        "puuid": puuid,
                        "championId": champion,
                        "teamPosition": position,
                        "riotIdGameName": f"name-{puuid}",
                    }
                    for index, (puuid, champion, position) in enumerate(spec["participants"])
                ],
            },
        }
    ).encode()


def _timeline_body(match_id: str) -> bytes:
    return json.dumps(
        {"metadata": {"matchId": match_id}, "info": {"frames": [{"events": []}]}}
    ).encode()


def _response(match_id: str, kind: str, body: bytes) -> RawRiotResponse:
    return RawRiotResponse(
        match_id=match_id,
        kind=kind,
        body=body,
        sha256=hashlib.sha256(body).hexdigest(),
        fetched_at=datetime.now(UTC),
    )


@dataclass
class FakeTransport:
    world: World
    fail_at: dict[int, str] = field(default_factory=dict)
    requests_made: int = 0
    request_limit: int = 100_000
    time_limit_seconds: float = 100_000.0
    calls: list[tuple[str, str]] = field(default_factory=list)
    listing_params: list[dict[str, Any]] = field(default_factory=list)
    observer: Callable[[str], int] | None = None
    result_observer: (
        Callable[[int, str, int | None, float, str | None, str | None], None] | None
    ) = None
    local_budget_observer: Callable[[str, str], None] | None = None

    def set_request_observer(self, observer: Callable[[str], int]) -> None:
        assert self.requests_made == 0
        self.observer = observer

    def set_request_result_observer(
        self, observer: Callable[[int, str, int | None, float, str | None, str | None], None]
    ) -> None:
        self.result_observer = observer

    def set_local_budget_observer(self, observer: Callable[[str, str], None]) -> None:
        self.local_budget_observer = observer

    def set_request_limit(self, limit: int) -> None:
        assert self.requests_made == 0
        self.request_limit = limit

    def set_total_time_limit(self, seconds: float) -> None:
        assert self.requests_made == 0 and seconds > 0
        self.time_limit_seconds = seconds

    def _request(self, kind: str, subject: str, failure: str | None = None) -> None:
        failure = failure or self.fail_at.get(self.requests_made + 1)
        if failure == "local-budget" or self.requests_made >= self.request_limit:
            assert self.local_budget_observer is not None
            self.local_budget_observer(kind, "LOCAL_REQUEST_BUDGET")
            raise SourceRateLimitedError(code="RIOT_RATE_LIMITED", detail="orçamento local")
        assert self.observer is not None, "request observer was not installed"
        ordinal = self.observer(kind)
        self.requests_made += 1
        self.calls.append((kind, subject))
        result, status = {
            "auth": ("HTTP_AUTH", 401),
            "429": ("HTTP_429", 429),
            "5xx": ("HTTP_5XX", 503),
            "404": ("HTTP_NOT_FOUND", 404),
        }.get(failure or "", ("HTTP_200", 200))
        if self.result_observer is not None:
            self.result_observer(ordinal, result, status, 0.0, None, None)
        if failure == "auth":
            raise SourceAuthError(code="RIOT_AUTH_FAILED", detail="status 401")
        if failure == "429":
            raise SourceRateLimitedError(code="RIOT_RATE_LIMITED", detail="limite de taxa")
        if failure == "5xx":
            raise SourceError(code="UPSTREAM_UNAVAILABLE", detail="status 503")
        if failure == "404":
            raise SourceNotFoundError(code="RIOT_NOT_FOUND", detail=f"{kind} não encontrado")

    async def resolve_account_puuid(self, game_name: str, tag_line: str) -> str:
        puuid = self.world.accounts.get((game_name, tag_line))
        self._request("account", "seed", None if puuid else "404")
        assert puuid is not None
        return puuid

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
        self._request("match-list", puuid)
        self.listing_params.append(
            {
                "queue": queue,
                "start": start,
                "count": count,
                "startTime": start_time,
                "endTime": end_time,
            }
        )
        return list(self.world.listings.get(puuid, []))[start : start + count]

    async def fetch_match(self, match_id: str) -> RawRiotResponse:
        spec = self.world.matches.get(match_id)
        self._request("match", match_id, None if spec else "404")
        assert spec is not None
        return _response(match_id, "match", _match_body(match_id, spec))

    async def fetch_timeline(self, match_id: str) -> RawRiotResponse:
        self._request("timeline", match_id)
        return _response(match_id, "timeline", _timeline_body(match_id))

    def listed(self) -> list[str]:
        return [subject for kind, subject in self.calls if kind == "match-list"]

    def fetched(self, kind: str = "match") -> list[str]:
        return [subject for call_kind, subject in self.calls if call_kind == kind]


def _output_dir(tmp_path: Path) -> Path:
    private_dir(tmp_path / ".private" / "collection")
    return private_dir(tmp_path / ".private" / "collection" / "cc323")


async def _collect(
    output_dir: Path, transport: FakeTransport, *, attempt: int = 1, **kwargs: Any
) -> dataset.CollectionSummary:
    kwargs.setdefault("authorized_max_requests", 100)
    return await dataset.run_dataset_collection(
        transport=transport,
        seeds=kwargs.pop("seeds", [SEED]),
        output_dir=output_dir,
        attempt_number=attempt,
        **kwargs,
    )


def _snowball_world() -> World:
    world = World()
    world.listings = {
        "puuid-seed": ["BR1_1", "BR1_2", "BR1_3"],
        "puuid-scope": ["BR1_4"],
        "puuid-other-1": ["BR1_5"],
        "puuid-other-2": ["BR1_1"],
        "puuid-other-3": [],
        "puuid-untrusted": ["BR1_6"],
    }
    world.match(
        "BR1_1",
        [
            ("puuid-seed", OTHER, "MIDDLE"),
            ("puuid-scope", KAISA, "BOTTOM"),
            ("puuid-other-1", KAISA, "TOP"),
        ],
    )
    world.match(
        "BR1_2",
        [("puuid-seed", OTHER, "MIDDLE"), ("puuid-other-2", OTHER, "JUNGLE")],
        version="16.18.701.1",
    )
    world.match(
        "BR1_3",
        [("puuid-seed", OTHER, "MIDDLE"), ("puuid-untrusted", CAITLYN, "BOTTOM")],
        identity="BR1_999",
    )
    world.match(
        "BR1_4",
        [("puuid-scope", CAITLYN, "BOTTOM"), ("puuid-other-3", OTHER, "TOP")],
    )
    world.match("BR1_5", [("puuid-other-1", OTHER, "TOP")], result="Abort_TooFewPlayers")
    return world


def _private_text(output_dir: Path) -> str:
    return "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(output_dir.iterdir())
        if path.is_file() and path.suffix == ".json"
    )


@pytest.mark.asyncio
async def test_snowball_expands_through_all_participants_and_stores_only_target_pairs(
    tmp_path: Path,
) -> None:
    output_dir = _output_dir(tmp_path)
    transport = FakeTransport(_snowball_world())

    summary = await _collect(output_dir, transport)

    assert summary.status == "QUEUE_EXHAUSTED"
    assert transport.listed() == [
        "puuid-seed",
        "puuid-scope",
        "puuid-other-1",
        "puuid-other-2",
        "puuid-other-3",
    ]
    assert transport.fetched() == ["BR1_1", "BR1_2", "BR1_3", "BR1_4", "BR1_5"]
    assert transport.fetched("timeline") == ["BR1_1", "BR1_4"]
    assert all(
        params
        == {
            "queue": 420,
            "start": 0,
            "count": 5,
            "startTime": dataset.WINDOW_START,
            "endTime": dataset.WINDOW_END,
        }
        for params in transport.listing_params
    )
    assert (summary.pairs_new, summary.pairs_stored_total, summary.requests) == (2, 2, 13)
    assert summary.scope_slots_in_new_pairs == 2
    assert summary.discarded == {"IDENTITY_MISMATCH": 1, "NOT_COMPLETE": 1, "OTHER_PATCH": 1}
    store = DurableRawMatchStore(output_dir / "raw", create=False)
    for match_id in ("BR1_1", "BR1_4"):
        assert store.read_if_valid(match_id, "match") is not None
        assert store.read_if_valid(match_id, "timeline") is not None
    for match_id in ("BR1_2", "BR1_3", "BR1_5"):
        assert store.read_if_valid(match_id, "match") is None
    assert (output_dir / "raw").stat().st_mode & 0o777 == 0o700

    report_path = output_dir / "collection-attempt-01.json"
    assert report_path.stat().st_mode & 0o777 == 0o600
    assert hashlib.sha256(report_path.read_bytes()).hexdigest() == summary.report_sha256
    report = json.loads(report_path.read_text())
    assert report["status"] == "QUEUE_EXHAUSTED"
    assert report["newMatchIds"] == ["BR1_1", "BR1_4"]
    assert report["expansion"]["maximumParticipantHops"] is None
    assert report["caps"]["attemptRequestLimit"] == 100
    journal = json.loads((output_dir / "request-budget-01.json").read_text())
    assert (journal["status"], journal["requestsReserved"]) == ("COMPLETED", 13)
    assert {entry["requestClass"] for entry in journal["diagnostics"]} == {
        "account",
        "match-list",
        "match",
        "timeline",
    }
    text = _private_text(output_dir)
    for secret in ("puuid-", "name-", "FakeSeed", "TEST"):
        assert secret not in text
    assert (output_dir / ".collection-started-01").exists()


@pytest.mark.asyncio
async def test_untrusted_match_body_never_feeds_the_expansion(tmp_path: Path) -> None:
    transport = FakeTransport(_snowball_world())

    await _collect(_output_dir(tmp_path), transport)

    assert "puuid-untrusted" not in transport.listed()
    assert "BR1_6" not in transport.fetched()


@pytest.mark.asyncio
async def test_player_first_seen_outside_scope_is_promoted_when_seen_in_scope(
    tmp_path: Path,
) -> None:
    world = World()
    world.listings = {"puuid-seed": ["BR1_1", "BR1_2"]}
    world.match(
        "BR1_1",
        [
            ("puuid-seed", OTHER, "MIDDLE"),
            ("puuid-x", OTHER, "TOP"),
            ("puuid-y", OTHER, "JUNGLE"),
        ],
    )
    world.match("BR1_2", [("puuid-seed", OTHER, "MIDDLE"), ("puuid-y", EZREAL, "BOTTOM")])
    transport = FakeTransport(world)

    await _collect(_output_dir(tmp_path), transport)

    assert transport.listed() == ["puuid-seed", "puuid-y", "puuid-x"]


def _linear_world(count: int) -> World:
    world = World()
    world.listings = {"puuid-seed": [f"BR1_{index}" for index in range(1, count + 1)]}
    for index in range(1, count + 1):
        world.match(f"BR1_{index}", [("puuid-seed", KAISA, "BOTTOM")])
    return world


@pytest.mark.asyncio
async def test_pair_cap_stops_before_any_further_request(tmp_path: Path) -> None:
    transport = FakeTransport(_linear_world(3))

    summary = await _collect(_output_dir(tmp_path), transport, max_pairs=2)

    assert summary.status == "PAIR_CAP_REACHED"
    assert (summary.pairs_new, summary.requests) == (2, 6)
    assert "BR1_3" not in transport.fetched()


@pytest.mark.asyncio
async def test_request_cap_is_checked_before_each_request(tmp_path: Path) -> None:
    output_dir = _output_dir(tmp_path)
    transport = FakeTransport(_linear_world(3))

    summary = await _collect(output_dir, transport, authorized_max_requests=4)

    assert summary.status == "REQUEST_CAP_REACHED"
    assert (summary.pairs_new, summary.requests, transport.requests_made) == (1, 4, 4)
    assert transport.request_limit == 4
    journal = json.loads((output_dir / "request-budget-01.json").read_text())
    assert (journal["status"], journal["requestsReserved"]) == ("COMPLETED", 4)


@pytest.mark.asyncio
async def test_transport_local_request_budget_ends_with_report(tmp_path: Path) -> None:
    output_dir = _output_dir(tmp_path)
    transport = FakeTransport(_linear_world(3), fail_at={4: "local-budget"})

    summary = await _collect(output_dir, transport)

    assert summary.status == "REQUEST_CAP_REACHED"
    assert summary.pairs_new == 0
    journal = json.loads((output_dir / "request-budget-01.json").read_text())
    assert journal["status"] == "COMPLETED"
    assert journal["diagnostics"][-1]["result"] == "LOCAL_REQUEST_BUDGET"
    assert json.loads((output_dir / "collection-attempt-01.json").read_text())["status"] == (
        "REQUEST_CAP_REACHED"
    )


def _stepping_clock(step_ns: int) -> Callable[[], int]:
    ticks: Iterator[int] = iter(range(step_ns, 10**18, step_ns))
    return lambda: next(ticks)


@pytest.mark.asyncio
async def test_time_cap_keeps_the_shutdown_reserve(tmp_path: Path) -> None:
    transport = FakeTransport(_linear_world(3))

    summary = await _collect(
        _output_dir(tmp_path),
        transport,
        max_seconds=400,
        process_started_monotonic_ns=0,
        clock_ns=_stepping_clock(30_000_000_000),
    )

    assert summary.status == "TIME_CAP_REACHED"
    assert transport.requests_made == 2
    assert transport.time_limit_seconds == pytest.approx(70.0)


@pytest.mark.parametrize(
    ("failure", "status", "journal_status"),
    [
        ("auth", "STOPPED_AUTH", "STOPPED_AUTH"),
        ("429", "STOPPED_RATE_LIMITED", "STOPPED_RATE_OR_BUDGET"),
        ("5xx", "STOPPED_SOURCE_ERROR", "INTERRUPTED"),
    ],
)
@pytest.mark.asyncio
async def test_source_failure_ends_with_partial_report_and_keeps_stored_pairs(
    tmp_path: Path, failure: str, status: str, journal_status: str
) -> None:
    output_dir = _output_dir(tmp_path)
    transport = FakeTransport(_linear_world(3), fail_at={5: failure})

    summary = await _collect(output_dir, transport)

    assert summary.status == status
    assert summary.status not in dataset.DESIGNED_STOPS
    assert (summary.pairs_new, summary.requests) == (1, 5)
    journal = json.loads((output_dir / "request-budget-01.json").read_text())
    assert journal["status"] == journal_status
    if failure == "5xx":
        assert journal["failureCode"] == "UPSTREAM_UNAVAILABLE"
    report = json.loads((output_dir / "collection-attempt-01.json").read_text())
    assert (report["status"], report["newMatchIds"]) == (status, ["BR1_1"])
    store = DurableRawMatchStore(output_dir / "raw", create=False)
    assert store.read_if_valid("BR1_1", "timeline") is not None


@pytest.mark.asyncio
async def test_missing_match_or_listing_is_counted_and_skipped(tmp_path: Path) -> None:
    world = _linear_world(2)
    world.listings["puuid-seed"].insert(0, "BR1_404")
    transport = FakeTransport(world)

    summary = await _collect(_output_dir(tmp_path), transport)

    assert summary.status == "QUEUE_EXHAUSTED"
    assert summary.discarded == {"MATCH_NOT_FOUND": 1}
    assert summary.pairs_new == 2


def _store_pair(directory: Path, match_id: str, spec: dict[str, Any]) -> None:
    store = DurableRawMatchStore(private_dir(directory))
    for kind, body in (
        ("match", _match_body(match_id, spec)),
        ("timeline", _timeline_body(match_id)),
    ):
        store.store(match_id, kind, body, hashlib.sha256(body).hexdigest())


def _tree_state(directory: Path) -> dict[str, tuple[int, int]]:
    return {
        str(path.relative_to(directory)): (path.stat().st_size, path.stat().st_mtime_ns)
        for path in sorted(directory.rglob("*"))
    }


@pytest.mark.asyncio
async def test_pairs_known_elsewhere_cost_nothing_and_their_store_is_read_only(
    tmp_path: Path,
) -> None:
    world = World()
    world.listings = {"puuid-seed": ["BR1_1"], "puuid-scope": ["BR1_4"]}
    world.match("BR1_1", [("puuid-seed", OTHER, "MIDDLE"), ("puuid-scope", KAISA, "BOTTOM")])
    world.match("BR1_4", [("puuid-scope", KAISA, "BOTTOM")])
    known_directory = tmp_path / ".private" / "collection" / "cc316" / "raw-confirmation"
    private_dir(known_directory.parent)
    _store_pair(known_directory, "BR1_1", world.matches["BR1_1"])
    before = _tree_state(known_directory)
    output_dir = _output_dir(tmp_path)
    transport = FakeTransport(world)

    summary = await _collect(output_dir, transport, known_raw_directories=[known_directory])

    assert "BR1_1" not in transport.fetched()
    assert transport.listed() == ["puuid-seed", "puuid-scope"]
    assert (summary.pairs_known_elsewhere, summary.pairs_new) == (1, 1)
    assert _tree_state(known_directory) == before
    report = json.loads((output_dir / "collection-attempt-01.json").read_text())
    assert report["newMatchIds"] == ["BR1_4"]
    assert (
        DurableRawMatchStore(output_dir / "raw", create=False).read_if_valid("BR1_1", "match")
        is None
    )


def _write_lifecycle(output_dir: Path, attempt: int, elapsed_ns: int, boot_id: str) -> None:
    started = 1_000
    state = {
        "schemaVersion": 1,
        "attemptNumber": attempt,
        "lifecycleStatus": "COMPLETE",
        "runOutcome": "COMPLETED",
        "bootId": boot_id,
        "processStartedMonotonicNs": started,
        "coroutineStartedMonotonicNs": started + 1,
        "coroutineFinishedMonotonicNs": started + elapsed_ns - 1,
        "processFinishedMonotonicNs": started + elapsed_ns,
        "processElapsedNs": elapsed_ns,
        "shutdownElapsedNs": 1,
        "executorShutdownTimeoutNs": 300_000_000_000,
    }
    path = dataset.attempt_paths(output_dir, attempt)["lifecycle"]
    path.write_text(json.dumps(state), encoding="utf-8")


@pytest.mark.asyncio
async def test_resumed_attempt_sums_prior_usage_across_boots_and_reuses_stored_pairs(
    tmp_path: Path,
) -> None:
    output_dir = _output_dir(tmp_path)
    first = FakeTransport(_linear_world(3))
    await _collect(output_dir, first, authorized_max_requests=4)
    _write_lifecycle(output_dir, 1, 100_000_000_000, str(uuid.uuid4()))
    second = FakeTransport(_linear_world(3))

    summary = await _collect(
        output_dir, second, attempt=2, authorized_max_requests=10, max_requests=10
    )

    assert (summary.status, summary.requests, summary.pairs_new) == ("QUEUE_EXHAUSTED", 6, 2)
    assert "BR1_1" not in second.fetched()
    assert second.request_limit == 6
    report = json.loads((output_dir / "collection-attempt-02.json").read_text())
    assert report["caps"]["priorRequests"] == 4
    assert report["caps"]["priorProcessSeconds"] == 100.0
    assert report["counts"]["PAIR_ALREADY_PRESENT"] == 1
    assert report["pairsStoredTotal"] == 3

    _write_lifecycle(output_dir, 2, 50_000_000_000, str(uuid.uuid4()))
    assert dataset.prior_campaign_usage(output_dir, 3) == (4 + summary.requests, 150_000_000_000)


@pytest.mark.parametrize(
    "case", ["no_marker", "lifecycle_in_progress", "elapsed_mismatch", "journal_in_progress"]
)
@pytest.mark.asyncio
async def test_incoherent_campaign_history_blocks_the_next_attempt(
    tmp_path: Path, case: str
) -> None:
    output_dir = _output_dir(tmp_path)
    await _collect(output_dir, FakeTransport(_linear_world(1)))
    _write_lifecycle(output_dir, 1, 10_000, str(uuid.uuid4()))
    paths = dataset.attempt_paths(output_dir, 1)
    if case == "no_marker":
        paths["marker"].unlink()
    elif case in {"lifecycle_in_progress", "elapsed_mismatch"}:
        state = json.loads(paths["lifecycle"].read_text())
        if case == "lifecycle_in_progress":
            state["lifecycleStatus"] = "IN_PROGRESS"
        else:
            state["processElapsedNs"] += 1
        paths["lifecycle"].write_text(json.dumps(state))
    else:
        journal = json.loads(paths["budget"].read_text())
        journal["status"] = "IN_PROGRESS"
        paths["budget"].write_text(json.dumps(journal))
    transport = FakeTransport(_linear_world(1))

    with pytest.raises(DiscoveryError):
        await _collect(output_dir, transport, attempt=2)

    assert transport.calls == []
    assert not (output_dir / "request-budget-02.json").exists()


@pytest.mark.asyncio
async def test_local_fault_raises_and_marks_the_journal_interrupted(tmp_path: Path) -> None:
    output_dir = _output_dir(tmp_path)
    world = _linear_world(1)
    _store_pair(output_dir / "raw", "BR1_1", world.matches["BR1_1"])
    for blob in (output_dir / "raw" / "blobs").iterdir():
        blob.unlink()
    transport = FakeTransport(world)

    with pytest.raises(Exception, match="não existe mais em disco"):
        await _collect(output_dir, transport)

    journal = json.loads((output_dir / "request-budget-01.json").read_text())
    assert journal["status"] == "INTERRUPTED"
    assert not (output_dir / "collection-attempt-01.json").exists()


@pytest.mark.asyncio
async def test_cancellation_leaves_a_terminal_journal_for_the_next_attempt(
    tmp_path: Path,
) -> None:
    output_dir = _output_dir(tmp_path)

    class CancellingTransport(FakeTransport):
        async def fetch_timeline(self, match_id: str) -> RawRiotResponse:
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await _collect(output_dir, CancellingTransport(_linear_world(1)))

    journal = json.loads((output_dir / "request-budget-01.json").read_text())
    assert (journal["status"], journal["requestsReserved"]) == ("INTERRUPTED", 3)
    _write_lifecycle(output_dir, 1, 10_000, str(uuid.uuid4()))
    assert dataset.prior_campaign_usage(output_dir, 2) == (3, 10_000)


@pytest.mark.asyncio
async def test_other_platform_ids_are_skipped_without_requests(tmp_path: Path) -> None:
    world = _linear_world(1)
    world.listings["puuid-seed"].insert(0, "NA1_123")
    transport = FakeTransport(world)

    summary = await _collect(_output_dir(tmp_path), transport)

    assert "NA1_123" not in transport.fetched()
    assert summary.discarded == {"OUT_OF_PLATFORM_OR_INVALID_ID": 1}
    assert summary.matches_listed == 1


@pytest.mark.parametrize(
    "case",
    [
        "other_directory",
        "insecure_directory",
        "already_started",
        "used_transport",
        "no_seeds",
        "zero_requests",
        "above_campaign_cap",
        "campaign_requests_consumed",
    ],
)
@pytest.mark.asyncio
async def test_gates_block_before_any_request(tmp_path: Path, case: str) -> None:
    output_dir = _output_dir(tmp_path)
    transport = FakeTransport(_linear_world(1))
    kwargs: dict[str, Any] = {}
    attempt = 1
    if case == "other_directory":
        output_dir = private_dir(tmp_path / ".private" / "collection" / "cc316")
    elif case == "insecure_directory":
        output_dir.chmod(0o755)
    elif case == "already_started":
        dataset.attempt_paths(output_dir, 1)["marker"].write_text("x")
    elif case == "used_transport":
        transport.requests_made = 1
    elif case == "no_seeds":
        kwargs["seeds"] = []
    elif case == "zero_requests":
        kwargs["authorized_max_requests"] = 0
    elif case == "above_campaign_cap":
        kwargs["authorized_max_requests"] = dataset.MAX_REQUESTS + 1
    else:
        await _collect(
            output_dir, FakeTransport(_linear_world(1)), max_requests=3, authorized_max_requests=3
        )
        _write_lifecycle(output_dir, 1, 10_000, str(uuid.uuid4()))
        kwargs.update(max_requests=3, authorized_max_requests=3)
        attempt = 2

    with pytest.raises(DiscoveryError):
        await _collect(output_dir, transport, attempt=attempt, **kwargs)

    assert transport.calls == []
    if case != "used_transport":
        assert transport.requests_made == 0


def _write_authorization(
    tmp_path: Path, *, overrides: dict[str, object] | None = None, attempt: int = 1
) -> Path:
    directory = private_dir(tmp_path / ".private" / "validation" / "sprint-3" / "CC-323")
    record = directory / "99-user-decision-synthetic.md"
    record.write_text("decisão sintética", encoding="utf-8")
    state: dict[str, object] = {
        "schemaVersion": 1,
        "campaign": "CC-323",
        "scope": "DATASET_COLLECTION",
        "attemptNumber": attempt,
        "maxPairs": dataset.MAX_PAIRS,
        "maxRequests": 4_000,
        "maxSeconds": dataset.MAX_SECONDS,
        "window": {"startTime": dataset.WINDOW_START, "endTime": dataset.WINDOW_END},
        "seedsSha256": hashlib.sha256(SEEDS_BYTES).hexdigest(),
        "decisionRecord": {
            "path": "validation/sprint-3/CC-323/99-user-decision-synthetic.md",
            "sha256": hashlib.sha256(record.read_bytes()).hexdigest(),
        },
    }
    state.update(overrides or {})
    path = directory / f"99-authorization-attempt-{attempt:02d}.json"
    write_private_with_sidecar(path, (json.dumps(state, sort_keys=True) + "\n").encode())
    return path


def test_authorization_returns_caps_for_the_exact_attempt(tmp_path: Path) -> None:
    granted = dataset.load_collection_authorization(
        _write_authorization(tmp_path), attempt_number=1
    )

    assert granted == dataset.CollectionAuthorization(
        max_requests=4_000, seeds_sha256=hashlib.sha256(SEEDS_BYTES).hexdigest()
    )


@pytest.mark.parametrize(
    "case",
    [
        "other_attempt",
        "other_campaign",
        "other_scope",
        "other_pair_cap",
        "other_time_cap",
        "other_window",
        "zero_requests",
        "above_request_cap",
        "boolean_requests",
        "bad_seeds_hash",
        "extra_field",
        "record_changed",
        "record_traversal",
        "insecure_mode",
        "sidecar_mismatch",
    ],
)
def test_authorization_fails_closed(tmp_path: Path, case: str) -> None:
    overrides: dict[str, object] = {
        "other_campaign": {"campaign": "CC-316"},
        "other_scope": {"scope": "CONFIRMATION_FETCH"},
        "other_pair_cap": {"maxPairs": 2_000},
        "other_time_cap": {"maxSeconds": 9_000},
        "other_window": {"window": {"startTime": 0, "endTime": dataset.WINDOW_END}},
        "zero_requests": {"maxRequests": 0},
        "above_request_cap": {"maxRequests": 4_001},
        "boolean_requests": {"maxRequests": True},
        "bad_seeds_hash": {"seedsSha256": "xyz"},
        "extra_field": {"note": "texto livre"},
        "record_traversal": {"decisionRecord": {"path": "../outside.md", "sha256": "0" * 64}},
    }.get(case, {})
    path = _write_authorization(tmp_path, overrides=overrides)
    attempt = 2 if case == "other_attempt" else 1
    if case == "record_changed":
        (path.parent / "99-user-decision-synthetic.md").write_text("outra", encoding="utf-8")
    elif case == "insecure_mode":
        path.chmod(0o644)
    elif case == "sidecar_mismatch":
        path.with_suffix(".json.sha256").write_text(f"{'0' * 64}  {path.name}\n", encoding="ascii")

    with pytest.raises(DiscoveryError):
        dataset.load_collection_authorization(path, attempt_number=attempt)


def _forbidden(*_args: object, **_kwargs: object) -> None:
    raise AssertionError("must not run before the authorization and seeds gates")


def test_cli_rejects_invalid_authorization_before_settings_or_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output_dir = tmp_path / ".private" / "collection" / "cc323"
    monkeypatch.setattr("riftlab.cli.env_file.load_settings", _forbidden)
    monkeypatch.setattr(exploration_module, "connectivity_preflight", _forbidden)

    with pytest.raises(typer.Exit):
        collect_dataset_16_19(
            attempt=1,
            authorization=tmp_path / ".private" / "missing-authorization.json",
            seeds_file=tmp_path / ".private" / "seeds.yaml",
            output_dir=output_dir,
            env_file=None,
        )

    assert not output_dir.exists()


def test_cli_rejects_unauthorized_seeds_before_settings_or_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    authorization = _write_authorization(tmp_path)
    seeds_file = private_dir(tmp_path / ".private" / "collection") / "seeds.yaml"
    seeds_file.write_bytes(SEEDS_BYTES + b"# alterado\n")
    monkeypatch.setattr("riftlab.cli.env_file.load_settings", _forbidden)
    monkeypatch.setattr(exploration_module, "connectivity_preflight", _forbidden)
    output_dir = tmp_path / ".private" / "collection" / "cc323"

    with pytest.raises(typer.Exit):
        collect_dataset_16_19(
            attempt=1,
            authorization=authorization,
            seeds_file=seeds_file,
            output_dir=output_dir,
            env_file=None,
        )

    assert [path.name for path in output_dir.iterdir()] == []


def test_cli_runs_one_attempt_with_lifecycle_and_prints_aggregates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    authorization = _write_authorization(tmp_path)
    seeds_file = private_dir(tmp_path / ".private" / "collection") / "seeds.yaml"
    seeds_file.write_bytes(SEEDS_BYTES)
    monkeypatch.setattr("riftlab.collection.seeds.load_seeds", lambda _path: [SEED])
    monkeypatch.setattr(
        "riftlab.cli.env_file.load_settings",
        lambda _env_file: SimpleNamespace(riot_api_key="synthetic-key"),
    )
    monkeypatch.setattr(exploration_module, "connectivity_preflight", lambda: None)
    world = _snowball_world()
    built: list[FakeTransport] = []

    class CliTransport(FakeTransport):
        def __init__(self, **kwargs: Any) -> None:
            assert kwargs["api_key"] == "synthetic-key"
            assert kwargs["budget"].max_requests == dataset.MAX_REQUESTS
            super().__init__(world)
            built.append(self)

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(
        "riftlab.ingestion.infrastructure.riot_transport.RiotHttpTransport", CliTransport
    )
    output_dir = tmp_path / ".private" / "collection" / "cc323"

    collect_dataset_16_19(
        attempt=1,
        authorization=authorization,
        seeds_file=seeds_file,
        output_dir=output_dir,
        env_file=None,
    )

    printed = json.loads(capsys.readouterr().out)
    assert (printed["status"], printed["pairs_new"], printed["requests"]) == (
        "QUEUE_EXHAUSTED",
        2,
        13,
    )
    assert "puuid-" not in json.dumps(printed)
    assert built[0].request_limit == 4_000
    lifecycle = json.loads((output_dir / "process-lifecycle-01.json").read_text())
    assert (lifecycle["lifecycleStatus"], lifecycle["runOutcome"]) == ("COMPLETE", "COMPLETED")
    assert dataset.prior_campaign_usage(output_dir, 2)[0] == 13


# --- CC-703: the CC-702 scope at scale, with a spread listing --------------------------

JINX = 222


def _output_dir_703(tmp_path: Path) -> Path:
    private_dir(tmp_path / ".private" / "collection")
    return private_dir(tmp_path / ".private" / "collection" / "cc703")


def _wide_world(count: int = 20) -> World:
    world = World()
    world.listings = {"puuid-seed": [f"BR1_{n}" for n in range(1, count + 1)]}
    for n in range(1, count + 1):
        world.match(f"BR1_{n}", [("puuid-seed", OTHER, "MIDDLE")])
    return world


@pytest.mark.asyncio
async def test_cc703_lists_the_whole_window_and_keeps_a_reproducible_spread_sample(
    tmp_path: Path,
) -> None:
    runs = []
    for name in ("a", "b"):
        output_dir = _output_dir_703(tmp_path / name)
        transport = FakeTransport(_wide_world())
        summary = await _collect(output_dir, transport, campaign=dataset.CC703)
        runs.append((transport, summary, output_dir))

    first, second = runs[0][0], runs[1][0]
    assert first.listing_params == [
        {
            "queue": 420,
            "start": 0,
            "count": 100,
            "startTime": dataset.WINDOW_START,
            "endTime": dataset.WINDOW_END,
        }
    ]
    sampled = first.fetched()
    assert len(sampled) == 5 and sampled == second.fetched()
    # Not the five most recent: the sample spreads over the player's listing.
    assert sampled != [f"BR1_{n}" for n in range(1, 6)]
    assert set(sampled) == set(
        dataset.CC703.keep("puuid-seed", _wide_world().listings["puuid-seed"])
    )
    report = json.loads((runs[0][2] / "collection-attempt-01.json").read_text(encoding="utf-8"))
    assert report["campaign"] == "CC-703"
    assert report["expansion"]["listingSelection"] == "SALTED_HASH_SAMPLE"
    assert report["expansion"]["matchesListedPerPlayer"] == 5
    assert report["expansion"]["scope"]["championIds"] == sorted(dataset.CC703.scope_champions)
    assert report["counts"]["LISTED_IDS_NOT_SAMPLED"] == 15
    assert "LISTED_IDS_NOT_SAMPLED" not in runs[0][1].discarded


@pytest.mark.asyncio
async def test_cc703_lists_players_of_the_ten_marksmen_first(tmp_path: Path) -> None:
    world = World()
    world.listings = {"puuid-seed": ["BR1_1"], "puuid-jinx": [], "puuid-other": []}
    world.match(
        "BR1_1",
        [
            ("puuid-seed", OTHER, "MIDDLE"),
            ("puuid-other", OTHER, "TOP"),
            ("puuid-jinx", JINX, "BOTTOM"),
        ],
    )
    transport = FakeTransport(world)

    await _collect(_output_dir_703(tmp_path), transport, campaign=dataset.CC703)

    assert transport.listed() == ["puuid-seed", "puuid-jinx", "puuid-other"]
    # The CC-323 scope keeps Jinx with everyone else.
    legacy = FakeTransport(world)
    await _collect(_output_dir(tmp_path / "legacy"), legacy)
    assert legacy.listed() == ["puuid-seed", "puuid-other", "puuid-jinx"]


@pytest.mark.asyncio
async def test_cc703_reuses_cc323_pairs_without_requests_and_owns_its_directory(
    tmp_path: Path,
) -> None:
    world = _snowball_world()
    cc323_raw = private_dir(_output_dir(tmp_path) / "raw")
    _store_pair(cc323_raw, "BR1_4", world.matches["BR1_4"])
    output_dir = _output_dir_703(tmp_path)
    transport = FakeTransport(world)

    await _collect(output_dir, transport, campaign=dataset.CC703, known_raw_directories=[cc323_raw])

    assert "BR1_4" not in transport.fetched()
    with pytest.raises(DiscoveryError, match="cc703"):
        await _collect(
            _output_dir(tmp_path / "wrong"), FakeTransport(world), campaign=dataset.CC703
        )


def _write_authorization_703(tmp_path: Path, **overrides: object) -> Path:
    path = _write_authorization(
        tmp_path,
        overrides={
            "campaign": "CC-703",
            "maxPairs": 10_000,
            "maxRequests": 24_000,
            "maxSeconds": 36_000,
            **overrides,
        },
    )
    return path


def test_cc703_authorization_is_bound_to_its_own_caps(tmp_path: Path) -> None:
    granted = dataset.load_collection_authorization(
        _write_authorization_703(tmp_path), attempt_number=1, campaign=dataset.CC703
    )
    assert granted.max_requests == 24_000
    for name, overrides in {
        "legacy_caps": {"maxPairs": dataset.MAX_PAIRS, "maxSeconds": dataset.MAX_SECONDS},
        "above_cap": {"maxRequests": 24_001},
        "other_campaign": {"campaign": "CC-323"},
    }.items():
        path = _write_authorization_703(tmp_path / name, **overrides)
        with pytest.raises(DiscoveryError, match="CC-703"):
            dataset.load_collection_authorization(path, attempt_number=1, campaign=dataset.CC703)
    # A CC-703 authorization never opens the CC-323 campaign.
    with pytest.raises(DiscoveryError, match="CC-323"):
        dataset.load_collection_authorization(
            _write_authorization_703(tmp_path / "cross"), attempt_number=1
        )


def test_cli_scale_command_runs_the_cc703_campaign(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from riftlab.cli.collect import collect_dataset_16_19_scale

    authorization = _write_authorization_703(tmp_path)
    seeds_file = private_dir(tmp_path / ".private" / "collection") / "seeds.yaml"
    seeds_file.write_bytes(SEEDS_BYTES)
    monkeypatch.setattr("riftlab.collection.seeds.load_seeds", lambda _path: [SEED])
    monkeypatch.setattr(
        "riftlab.cli.env_file.load_settings",
        lambda _env_file: SimpleNamespace(riot_api_key="synthetic-key"),
    )
    monkeypatch.setattr(exploration_module, "connectivity_preflight", lambda: None)
    world = _snowball_world()
    cc323_raw = private_dir(_output_dir(tmp_path) / "raw")
    _store_pair(cc323_raw, "BR1_4", world.matches["BR1_4"])
    built: list[FakeTransport] = []

    class CliTransport(FakeTransport):
        def __init__(self, **kwargs: Any) -> None:
            assert kwargs["budget"].max_requests == 24_000
            assert kwargs["budget"].max_matches == 10_000
            super().__init__(world)
            built.append(self)

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(
        "riftlab.ingestion.infrastructure.riot_transport.RiotHttpTransport", CliTransport
    )
    output_dir = tmp_path / ".private" / "collection" / "cc703"

    collect_dataset_16_19_scale(
        attempt=1,
        authorization=authorization,
        seeds_file=seeds_file,
        output_dir=output_dir,
        env_file=None,
    )

    printed = json.loads(capsys.readouterr().out)
    assert printed["status"] == "QUEUE_EXHAUSTED" and "puuid-" not in json.dumps(printed)
    assert built[0].request_limit == 24_000
    assert "BR1_4" not in built[0].fetched()
    assert (
        json.loads((output_dir / "collection-attempt-01.json").read_text())["campaign"] == "CC-703"
    )
