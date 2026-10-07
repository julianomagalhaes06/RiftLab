"""CC-316-B exploration fetch: authorization, connectivity preflight and runner.

Every fixture is synthetic: no test opens the network, the real private
collection directory or a database.
"""

from __future__ import annotations

import hashlib
import json
import socket
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import typer

import riftlab.collection.cc316_adoption as adoption_module
import riftlab.collection.cc316_discovery as discovery_module
import riftlab.collection.cc316_exploration as exploration_module
from riftlab.cli.collect import collect_cc316_confirmation_fetch, collect_cc316_exploration_fetch
from riftlab.collection.cc316_adoption import persist_adopted_partition_once
from riftlab.collection.cc316_discovery import DiscoveryError, create_operational_budget_baseline
from riftlab.collection.cc316_exploration import (
    connectivity_preflight,
    load_adopted_confirmation_ids,
    load_exploration_fetch_authorization,
    run_cc316_exploration_fetch,
    run_cc316_partition_fetch,
)
from riftlab.ingestion.infrastructure.raw_store import DurableRawMatchStore
from riftlab.ingestion.infrastructure.riot_transport import RawRiotResponse
from riftlab.shared.errors import SourceAuthError, SourceNotFoundError
from support.cc316_partition import (
    FAKE_AMENDMENT_SHA256,
    derive,
    private_dir,
    write_amendment,
    write_private_with_sidecar,
)

FAKE_ADOPTED_SHA256 = "cd" * 32


def _write_authorization(
    tmp_path: Path,
    *,
    adopted_sha256: str,
    amendment_sha256: str = FAKE_AMENDMENT_SHA256,
    overrides: dict[str, object] | None = None,
) -> Path:
    directory = private_dir(tmp_path / ".private" / "validation" / "sprint-3" / "CC-320")
    record = directory / "99-user-authorization-synthetic.md"
    record.write_text("decisão sintética", encoding="utf-8")
    state: dict[str, object] = {
        "schemaVersion": 1,
        "protocol": "CC-316-B",
        "scope": "EXPLORATION_FETCH",
        "attemptNumber": 7,
        "adoptedPartitionSha256": adopted_sha256,
        "amendmentSha256": amendment_sha256,
        "maxRequests": 60,
        "decisionRecord": {
            "path": "validation/sprint-3/CC-320/99-user-authorization-synthetic.md",
            "sha256": hashlib.sha256(record.read_bytes()).hexdigest(),
        },
    }
    state.update(overrides or {})
    path = directory / "99-authorization-attempt-07.json"
    write_private_with_sidecar(path, (json.dumps(state, sort_keys=True) + "\n").encode())
    return path


def test_authorization_returns_request_cap_for_the_exact_attempt(tmp_path: Path) -> None:
    path = _write_authorization(tmp_path, adopted_sha256=FAKE_ADOPTED_SHA256)

    assert (
        load_exploration_fetch_authorization(
            path,
            attempt_number=7,
            adopted_partition_sha256=FAKE_ADOPTED_SHA256,
            amendment_sha256=FAKE_AMENDMENT_SHA256,
        )
        == 60
    )


@pytest.mark.parametrize(
    "case",
    [
        "other_attempt",
        "other_partition",
        "unpinned_partition",
        "other_amendment",
        "other_scope",
        "record_changed",
        "record_traversal",
        "insecure_mode",
        "sidecar_mismatch",
        "extra_field",
        "zero_requests",
    ],
)
def test_authorization_fails_closed(tmp_path: Path, case: str) -> None:
    overrides: dict[str, object] = {
        "other_partition": {"adoptedPartitionSha256": "ef" * 32},
        "other_amendment": {"amendmentSha256": "ef" * 32},
        "other_scope": {"scope": "CONFIRMATION_FETCH"},
        "record_traversal": {"decisionRecord": {"path": "../outside.md", "sha256": "0" * 64}},
        "extra_field": {"note": "texto livre"},
        "zero_requests": {"maxRequests": 0},
    }.get(case, {})
    path = _write_authorization(tmp_path, adopted_sha256=FAKE_ADOPTED_SHA256, overrides=overrides)
    attempt, expected_partition = 7, FAKE_ADOPTED_SHA256
    if case == "other_attempt":
        attempt = 8
    elif case == "unpinned_partition":
        expected_partition = ""
    elif case == "record_changed":
        (path.parent / "99-user-authorization-synthetic.md").write_text("outra", encoding="utf-8")
    elif case == "insecure_mode":
        path.chmod(0o644)
    elif case == "sidecar_mismatch":
        path.with_suffix(".json.sha256").write_text(f"{'0' * 64}  {path.name}\n", encoding="ascii")

    with pytest.raises(DiscoveryError):
        load_exploration_fetch_authorization(
            path,
            attempt_number=attempt,
            adopted_partition_sha256=expected_partition,
            amendment_sha256=FAKE_AMENDMENT_SHA256,
        )


def test_preflight_fails_fast_when_dns_hangs() -> None:
    release = threading.Event()
    connections: list[object] = []

    def hanging_resolver(host: str, port: int, family: int, kind: int) -> list[Any]:
        release.wait(5)
        return []

    started = time.monotonic()
    with pytest.raises(DiscoveryError, match="DNS sem resposta"):
        connectivity_preflight(
            timeout_seconds=0.2,
            resolver=hanging_resolver,
            connector=lambda address, timeout: connections.append(address),  # type: ignore[arg-type,return-value]
        )
    release.set()
    assert time.monotonic() - started < 2
    assert connections == []


def test_preflight_reports_dns_and_tcp_failures() -> None:
    def failing_resolver(host: str, port: int, family: int, kind: int) -> list[Any]:
        raise socket.gaierror(socket.EAI_NONAME, "synthetic")

    def resolver(host: str, port: int, family: int, kind: int) -> list[Any]:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.1", port))]

    def refusing_connector(address: tuple[str, int], timeout: float) -> socket.socket:
        raise ConnectionRefusedError("synthetic")

    with pytest.raises(DiscoveryError, match="DNS indisponível"):
        connectivity_preflight(
            timeout_seconds=1, resolver=failing_resolver, connector=refusing_connector
        )
    with pytest.raises(DiscoveryError, match="conexão TCP falhou"):
        connectivity_preflight(timeout_seconds=1, resolver=resolver, connector=refusing_connector)


def test_preflight_opens_and_closes_tcp_without_sending_bytes() -> None:
    calls: dict[str, object] = {}

    class FakeConnection:
        closed = False

        def close(self) -> None:
            self.closed = True

        def sendall(self, data: bytes) -> None:
            raise AssertionError("preflight must not send bytes")

    connection = FakeConnection()

    def resolver(host: str, port: int, family: int, kind: int) -> list[Any]:
        calls["resolve"] = (host, port, family, kind)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.1", port))]

    def connector(address: tuple[str, int], timeout: float) -> Any:
        calls["connect"] = (address, timeout)
        return connection

    connectivity_preflight(timeout_seconds=1.5, resolver=resolver, connector=connector)

    assert calls["resolve"] == ("americas.api.riotgames.com", 443, 0, socket.SOCK_STREAM)
    assert calls["connect"] == (("192.0.2.1", 443), 1.5)
    assert connection.closed


def _match_body(match_id: str, *, version: str = "16.19.715.1234") -> bytes:
    players = [f"player-{match_id}-{index}" for index in range(10)]
    return json.dumps(
        {
            "metadata": {"matchId": match_id, "participants": players},
            "info": {
                "gameVersion": version,
                "queueId": 420,
                "mapId": 11,
                "endOfGameResult": "GameComplete",
                "participants": [
                    {"participantId": index + 1, "puuid": puuid, "item0": 1055}
                    for index, puuid in enumerate(players)
                ],
            },
        }
    ).encode()


def _timeline_body(match_id: str) -> bytes:
    return json.dumps(
        {
            "metadata": {"matchId": match_id, "participants": []},
            "info": {"frames": [{"events": [{"type": "ITEM_PURCHASED", "timestamp": 1}]}]},
        }
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
class FakeExplorationTransport:
    missing_match: set[str] = field(default_factory=set)
    missing_timeline: set[str] = field(default_factory=set)
    mismatched_match: set[str] = field(default_factory=set)
    wrong_timeline_identity: set[str] = field(default_factory=set)
    auth_failure: bool = False
    requests_made: int = 0
    request_limit: int = 1_000
    time_limit_seconds: float = 3_600.0
    calls: list[tuple[str, str]] = field(default_factory=list)
    observer: Callable[[str], int] | None = None
    result_observer: (
        Callable[[int, str, int | None, float, str | None, str | None], None] | None
    ) = None

    def set_request_observer(self, observer: Callable[[str], int]) -> None:
        self.observer = observer

    def set_request_result_observer(
        self, observer: Callable[[int, str, int | None, float, str | None, str | None], None]
    ) -> None:
        self.result_observer = observer

    def set_local_budget_observer(self, observer: Callable[[str, str], None]) -> None:
        return None

    def set_request_limit(self, limit: int) -> None:
        assert self.requests_made == 0
        self.request_limit = limit

    def set_total_time_limit(self, seconds: float) -> None:
        assert self.requests_made == 0 and seconds > 0
        self.time_limit_seconds = seconds

    def _request(
        self, kind: str, match_id: str, result: str = "HTTP_200", status: int = 200
    ) -> None:
        if self.requests_made >= self.request_limit:
            raise DiscoveryError("request cap exceeded")
        if self.observer is None:
            raise AssertionError("request observer was not installed")
        ordinal = self.observer(kind)
        self.requests_made += 1
        self.calls.append((kind, match_id))
        if self.result_observer is not None:
            self.result_observer(ordinal, result, status, 0.0, None, None)

    async def fetch_match(self, match_id: str) -> RawRiotResponse:
        if self.auth_failure:
            self._request("match", match_id, "HTTP_AUTH", 401)
            raise SourceAuthError(code="RIOT_AUTH_FAILED", detail="status 401")
        if match_id in self.missing_match:
            self._request("match", match_id, "HTTP_NOT_FOUND", 404)
            raise SourceNotFoundError(code="RIOT_NOT_FOUND", detail="match não encontrado")
        self._request("match", match_id)
        version = "16.18.700.1" if match_id in self.mismatched_match else "16.19.715.1234"
        return _response(match_id, "match", _match_body(match_id, version=version))

    async def fetch_timeline(self, match_id: str) -> RawRiotResponse:
        if match_id in self.missing_timeline:
            self._request("timeline", match_id, "HTTP_NOT_FOUND", 404)
            raise SourceNotFoundError(code="RIOT_NOT_FOUND", detail="timeline não encontrado")
        self._request("timeline", match_id)
        identity = "BR1_999999" if match_id in self.wrong_timeline_identity else match_id
        return _response(match_id, "timeline", _timeline_body(identity))


@dataclass
class ExplorationSetup:
    output_dir: Path
    adopted_sha256: str
    baseline: Path
    artifact: dict[str, Any]

    @property
    def exploration(self) -> list[str]:
        return list(self.artifact["partition"]["explorationMatchIds"])


def _exploration_setup(tmp_path: Path) -> ExplorationSetup:
    output_dir = private_dir(tmp_path / ".private" / "collection" / "cc316")
    artifact = derive()
    adopted_sha256 = persist_adopted_partition_once(
        output_dir / "partition-cc316b-adopted.json", artifact
    )
    baseline = private_dir(tmp_path / ".private" / "validation" / "CC-320") / "baseline.json"
    create_operational_budget_baseline(baseline)
    return ExplorationSetup(output_dir, adopted_sha256, baseline, artifact)


async def _fetch(
    setup: ExplorationSetup,
    transport: FakeExplorationTransport,
    *,
    attempt: int = 7,
    max_requests: int = 60,
) -> Any:
    return await run_cc316_exploration_fetch(
        transport=transport,
        output_dir=setup.output_dir,
        attempt_number=attempt,
        authorized_max_requests=max_requests,
        adopted_partition_sha256=setup.adopted_sha256,
        temporal_baseline_path=setup.baseline,
    )


def _store_pair(raw_directory: Path, match_id: str, *, timeline: bool = True) -> None:
    store = DurableRawMatchStore(private_dir(raw_directory))
    bodies = [("match", _match_body(match_id))]
    if timeline:
        bodies.append(("timeline", _timeline_body(match_id)))
    for kind, body in bodies:
        store.store(match_id, kind, body, hashlib.sha256(body).hexdigest())


@pytest.mark.asyncio
async def test_exploration_fetch_stores_only_exploration_pairs_with_durable_reservations(
    tmp_path: Path,
) -> None:
    setup = _exploration_setup(tmp_path)
    transport = FakeExplorationTransport()

    summary = await _fetch(setup, transport)

    partition = setup.artifact["partition"]
    assert summary.status == "COMPLETED"
    assert (summary.pairs_fetched, summary.requests) == (20, 40)
    assert [match_id for _, match_id in transport.calls] == [
        match_id for match_id in setup.exploration for _ in ("match", "timeline")
    ]
    hidden = (
        set(partition["confirmationPotentialMatchIds"])
        | set(partition["overlapExcludedMatchIds"])
        | set(partition["outOfScopeMatchIds"])
    )
    assert hidden.isdisjoint(match_id for _, match_id in transport.calls)
    raw_directory = setup.output_dir / "raw-exploration"
    assert raw_directory.stat().st_mode & 0o777 == 0o700
    store = DurableRawMatchStore(raw_directory, create=False)
    assert all(
        store.read_if_valid(match_id, kind) is not None
        for match_id in setup.exploration
        for kind in ("match", "timeline")
    )
    journal = json.loads((setup.output_dir / "request-budget-07.json").read_text())
    assert (journal["schemaVersion"], journal["status"], journal["requestsReserved"]) == (
        3,
        "COMPLETED",
        40,
    )
    assert {entry["requestClass"] for entry in journal["diagnostics"]} == {"match", "timeline"}
    assert {entry["result"] for entry in journal["diagnostics"]} == {"HTTP_200"}
    report_path = setup.output_dir / "exploration-fetch-attempt-07.json"
    assert report_path.stat().st_mode & 0o777 == 0o600
    assert hashlib.sha256(report_path.read_bytes()).hexdigest() == summary.report_sha256
    report = json.loads(report_path.read_text())
    assert report["status"] == "COMPLETED"
    assert report["explorationMatchIds"] == setup.exploration
    assert report["counts"] == {"PAIR_FETCHED": 20}
    assert "player-" not in report_path.read_text()
    assert (setup.output_dir / ".cc316-discovery-consumed-07").exists()


@pytest.mark.asyncio
async def test_exploration_fetch_resumes_without_refetching_stored_pairs(tmp_path: Path) -> None:
    setup = _exploration_setup(tmp_path)
    raw_directory = setup.output_dir / "raw-exploration"
    for match_id in setup.exploration[:5]:
        _store_pair(raw_directory, match_id)
    half_stored = setup.exploration[5]
    _store_pair(raw_directory, half_stored, timeline=False)
    transport = FakeExplorationTransport()

    summary = await _fetch(setup, transport, max_requests=29)

    assert summary.requests == 29
    assert (summary.pairs_already_present, summary.pairs_fetched) == (5, 15)
    assert ("match", half_stored) not in transport.calls
    assert ("timeline", half_stored) in transport.calls
    assert set(setup.exploration[:5]).isdisjoint(match_id for _, match_id in transport.calls)


@pytest.mark.asyncio
async def test_exploration_fetch_counts_failures_without_replacement(tmp_path: Path) -> None:
    setup = _exploration_setup(tmp_path)
    missing, mismatched, no_timeline, wrong_identity = setup.exploration[:4]
    transport = FakeExplorationTransport(
        missing_match={missing},
        mismatched_match={mismatched},
        missing_timeline={no_timeline},
        wrong_timeline_identity={wrong_identity},
    )

    summary = await _fetch(setup, transport)

    assert summary.status == "PARTIAL"
    assert summary.requests == 1 + 1 + 2 + 2 + 16 * 2
    assert (
        summary.match_not_found,
        summary.timeline_not_found,
        summary.identity_or_structure_mismatch,
        summary.pairs_fetched,
    ) == (1, 1, 2, 16)
    assert ("timeline", mismatched) not in transport.calls
    store = DurableRawMatchStore(setup.output_dir / "raw-exploration", create=False)
    assert store.read_if_valid(missing, "match") is None
    assert store.read_if_valid(mismatched, "match") is None
    assert store.read_if_valid(no_timeline, "match") is not None
    assert store.read_if_valid(no_timeline, "timeline") is None
    assert store.read_if_valid(wrong_identity, "timeline") is None
    report = json.loads((setup.output_dir / "exploration-fetch-attempt-07.json").read_text())
    assert report["explorationMatchIds"] == setup.exploration
    assert report["resultByMatchId"][mismatched]["status"] == "IDENTITY_OR_STRUCTURE_MISMATCH"
    assert report["resultByMatchId"][wrong_identity]["status"] == "IDENTITY_OR_STRUCTURE_MISMATCH"


@pytest.mark.asyncio
async def test_exploration_fetch_stops_on_auth_failure_without_report(tmp_path: Path) -> None:
    setup = _exploration_setup(tmp_path)
    transport = FakeExplorationTransport(auth_failure=True)

    with pytest.raises(SourceAuthError):
        await _fetch(setup, transport)

    journal = json.loads((setup.output_dir / "request-budget-07.json").read_text())
    assert (journal["status"], journal["requestsReserved"]) == ("STOPPED_AUTH", 1)
    assert not (setup.output_dir / "exploration-fetch-attempt-07.json").exists()


@pytest.mark.parametrize(
    "case",
    [
        "attempt_six",
        "used_transport",
        "missing_baseline",
        "wrong_adopted_hash",
        "already_started",
        "authorization_below_pending",
        "nothing_pending",
    ],
)
@pytest.mark.asyncio
async def test_exploration_gates_block_before_any_request(tmp_path: Path, case: str) -> None:
    setup = _exploration_setup(tmp_path)
    transport = FakeExplorationTransport()
    attempt, max_requests, adopted_sha256 = 7, 60, setup.adopted_sha256
    if case == "attempt_six":
        attempt = 6
    elif case == "used_transport":
        transport.requests_made = 1
    elif case == "missing_baseline":
        setup.baseline.unlink()
    elif case == "wrong_adopted_hash":
        adopted_sha256 = "0" * 64
    elif case == "already_started":
        (setup.output_dir / ".cc316-discovery-consumed-07").write_text("x", encoding="utf-8")
    elif case == "authorization_below_pending":
        max_requests = 39
    else:
        for match_id in setup.exploration:
            _store_pair(setup.output_dir / "raw-exploration", match_id)

    with pytest.raises(DiscoveryError):
        await run_cc316_exploration_fetch(
            transport=transport,
            output_dir=setup.output_dir,
            attempt_number=attempt,
            authorized_max_requests=max_requests,
            adopted_partition_sha256=adopted_sha256,
            temporal_baseline_path=setup.baseline,
        )

    assert transport.calls == []
    assert not (setup.output_dir / "request-budget-07.json").exists()
    if case != "already_started":
        assert not (setup.output_dir / ".cc316-discovery-consumed-07").exists()


def _forbidden(*_args: object, **_kwargs: object) -> None:
    raise AssertionError("must not run before the authorization gate")


def test_cli_rejects_invalid_authorization_before_settings_or_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output_dir = private_dir(tmp_path / ".private" / "collection" / "cc316")
    monkeypatch.setattr("riftlab.cli.env_file.load_settings", _forbidden)
    monkeypatch.setattr(exploration_module, "connectivity_preflight", _forbidden)

    with pytest.raises(typer.Exit):
        collect_cc316_exploration_fetch(
            attempt=7,
            authorization=tmp_path / ".private" / "missing-authorization.json",
            output_dir=output_dir,
            env_file=None,
        )

    assert list(output_dir.iterdir()) == []


def test_cli_preflight_failure_creates_no_attempt_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    setup = _exploration_setup(tmp_path)
    amendment_sha256 = write_amendment(tmp_path / ".private")
    monkeypatch.setattr(adoption_module, "AMENDMENT_SHA256", amendment_sha256)
    monkeypatch.setattr(adoption_module, "ADOPTED_PARTITION_SHA256", setup.adopted_sha256)
    monkeypatch.setattr(discovery_module, "temporal_budget_baseline_path", lambda: setup.baseline)
    authorization = _write_authorization(
        tmp_path, adopted_sha256=setup.adopted_sha256, amendment_sha256=amendment_sha256
    )
    monkeypatch.setattr(
        "riftlab.cli.env_file.load_settings",
        lambda _env_file: SimpleNamespace(riot_api_key="synthetic-key"),
    )

    def failing_preflight() -> None:
        raise DiscoveryError("preflight de rede: DNS sem resposta (provável sandbox sem rede)")

    monkeypatch.setattr(exploration_module, "connectivity_preflight", failing_preflight)

    with pytest.raises(typer.Exit):
        collect_cc316_exploration_fetch(
            attempt=7, authorization=authorization, output_dir=setup.output_dir, env_file=None
        )

    for name in (
        "process-lifecycle-07.json",
        "request-budget-07.json",
        ".cc316-discovery-consumed-07",
        "exploration-fetch-attempt-07.json",
    ):
        assert not (setup.output_dir / name).exists()


def _write_freeze(private: Path, text: str = "congelamento sintético") -> str:
    freeze = private / "validation" / "sprint-3" / "CC-321" / "12-congelamento-cc316.md"
    private_dir(freeze.parent)
    freeze.write_text(text, encoding="utf-8")
    digest = hashlib.sha256(freeze.read_bytes()).hexdigest()
    freeze.with_suffix(".md.sha256").write_text(f"{digest}  {freeze.name}\n", encoding="ascii")
    return digest


async def _fetch_confirmation(
    setup: ExplorationSetup,
    transport: FakeExplorationTransport,
    freeze_sha256: str,
    *,
    max_requests: int = 60,
) -> Any:
    return await run_cc316_partition_fetch(
        partition="confirmation",
        transport=transport,
        output_dir=setup.output_dir,
        attempt_number=7,
        authorized_max_requests=max_requests,
        adopted_partition_sha256=setup.adopted_sha256,
        freeze_sha256=freeze_sha256,
        temporal_baseline_path=setup.baseline,
    )


def test_confirmation_ids_are_released_only_with_the_sealed_freeze(tmp_path: Path) -> None:
    setup = _exploration_setup(tmp_path)
    adopted = setup.output_dir / "partition-cc316b-adopted.json"
    with pytest.raises(DiscoveryError, match="congelamento"):
        load_adopted_confirmation_ids(adopted, expected_sha256=setup.adopted_sha256)

    freeze_sha256 = _write_freeze(tmp_path / ".private")

    released = load_adopted_confirmation_ids(
        adopted, expected_sha256=setup.adopted_sha256, freeze_sha256=freeze_sha256
    )
    assert released == tuple(setup.artifact["partition"]["confirmationPotentialMatchIds"])
    assert set(released).isdisjoint(setup.exploration)


@pytest.mark.asyncio
async def test_confirmation_fetch_stores_only_confirmation_pairs(tmp_path: Path) -> None:
    setup = _exploration_setup(tmp_path)
    freeze_sha256 = _write_freeze(tmp_path / ".private")
    transport = FakeExplorationTransport()

    summary = await _fetch_confirmation(setup, transport, freeze_sha256)

    partition = setup.artifact["partition"]
    confirmation = partition["confirmationPotentialMatchIds"]
    assert (summary.partition, summary.status, summary.match_count) == (
        "confirmation",
        "COMPLETED",
        len(confirmation),
    )
    assert [match_id for _, match_id in transport.calls] == [
        match_id for match_id in confirmation for _ in ("match", "timeline")
    ]
    never = (
        set(setup.exploration)
        | set(partition["overlapExcludedMatchIds"])
        | set(partition["outOfScopeMatchIds"])
    )
    assert never.isdisjoint(match_id for _, match_id in transport.calls)
    assert not (setup.output_dir / "raw-exploration").exists()
    store = DurableRawMatchStore(setup.output_dir / "raw-confirmation", create=False)
    assert all(store.read_if_valid(match_id, "timeline") is not None for match_id in confirmation)
    report = json.loads((setup.output_dir / "confirmation-fetch-attempt-07.json").read_text())
    assert report["phase"] == "CONFIRMATION_FETCH"
    assert report["confirmationMatchIds"] == confirmation
    assert report["privacy"]["confirmationFetched"] is True
    assert report["privacy"]["eventsProcessed"] is False


@pytest.mark.parametrize("freeze_state", ["missing", "tampered", "other_pin"])
@pytest.mark.asyncio
async def test_confirmation_fetch_without_sealed_freeze_makes_no_request(
    tmp_path: Path, freeze_state: str
) -> None:
    setup = _exploration_setup(tmp_path)
    pinned = "0" * 64
    if freeze_state != "missing":
        pinned = _write_freeze(tmp_path / ".private")
    if freeze_state == "tampered":
        (tmp_path / ".private/validation/sprint-3/CC-321/12-congelamento-cc316.md").write_text(
            "alterado depois do selo", encoding="utf-8"
        )
    if freeze_state == "other_pin":
        pinned = "ab" * 32
    transport = FakeExplorationTransport()

    with pytest.raises(DiscoveryError, match="congelamento"):
        await _fetch_confirmation(setup, transport, pinned)

    assert transport.calls == []
    assert not (setup.output_dir / ".cc316-discovery-consumed-07").exists()
    assert not (setup.output_dir / "request-budget-07.json").exists()


def test_authorization_scopes_do_not_cross(tmp_path: Path) -> None:
    exploration_path = _write_authorization(tmp_path, adopted_sha256=FAKE_ADOPTED_SHA256)
    with pytest.raises(DiscoveryError, match="escopo"):
        load_exploration_fetch_authorization(
            exploration_path,
            attempt_number=7,
            adopted_partition_sha256=FAKE_ADOPTED_SHA256,
            amendment_sha256=FAKE_AMENDMENT_SHA256,
            scope="CONFIRMATION_FETCH",
        )
    confirmation_path = _write_authorization(
        tmp_path / "other",
        adopted_sha256=FAKE_ADOPTED_SHA256,
        overrides={"scope": "CONFIRMATION_FETCH"},
    )
    assert (
        load_exploration_fetch_authorization(
            confirmation_path,
            attempt_number=7,
            adopted_partition_sha256=FAKE_ADOPTED_SHA256,
            amendment_sha256=FAKE_AMENDMENT_SHA256,
            scope="CONFIRMATION_FETCH",
        )
        == 60
    )
    with pytest.raises(DiscoveryError, match="escopo"):
        load_exploration_fetch_authorization(
            confirmation_path,
            attempt_number=7,
            adopted_partition_sha256=FAKE_ADOPTED_SHA256,
            amendment_sha256=FAKE_AMENDMENT_SHA256,
        )


def test_cli_confirmation_fetch_without_freeze_blocks_before_settings_or_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    setup = _exploration_setup(tmp_path)
    amendment_sha256 = write_amendment(tmp_path / ".private")
    monkeypatch.setattr(adoption_module, "AMENDMENT_SHA256", amendment_sha256)
    monkeypatch.setattr(adoption_module, "ADOPTED_PARTITION_SHA256", setup.adopted_sha256)
    monkeypatch.setattr(discovery_module, "temporal_budget_baseline_path", lambda: setup.baseline)
    authorization = _write_authorization(
        tmp_path,
        adopted_sha256=setup.adopted_sha256,
        amendment_sha256=amendment_sha256,
        overrides={"scope": "CONFIRMATION_FETCH"},
    )
    monkeypatch.setattr("riftlab.cli.env_file.load_settings", _forbidden)
    monkeypatch.setattr(exploration_module, "connectivity_preflight", _forbidden)

    with pytest.raises(typer.Exit):
        collect_cc316_confirmation_fetch(
            attempt=7, authorization=authorization, output_dir=setup.output_dir, env_file=None
        )

    for name in (
        "process-lifecycle-07.json",
        "request-budget-07.json",
        ".cc316-discovery-consumed-07",
        "confirmation-fetch-attempt-07.json",
        "raw-confirmation",
    ):
        assert not (setup.output_dir / name).exists()
