"""CC-316-B raw fetch of one partition (match+timeline): exploration or confirmation.

The IDs come only from the pinned adopted partition; confirmation IDs are
released only after the sealed CC-316 freeze. Each attempt needs a user
authorization artifact for its scope, passes a connectivity preflight that
sends no request to Riot, and spends the cumulative CC-320 request/time budget.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import secrets
import socket
import stat
import threading
import time
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, cast

from riftlab.collection.cc316_adoption import (
    ADOPTED_PARTITION_FILENAME,
    ADOPTED_PARTITION_SHA256,
    AMENDMENT_SHA256,
    canonical_json,
    load_adopted_exploration_ids,
    private_root_for,
    read_private_with_sidecar,
)
from riftlab.collection.cc316_discovery import (
    _ASYNCIO_EXECUTOR_SHUTDOWN_RESERVE_NS,
    _MAX_DURATION_NS,
    MAX_REQUESTS,
    TARGET_MAP,
    TARGET_PATCH,
    TARGET_QUEUE,
    BudgetJournal,
    DiscoveryError,
    _atomic_write_once,
    current_boot_id,
    process_lifecycle_path,
    validate_prior_budget_history,
)
from riftlab.collection.metadata_partition import (
    StructuralMetadataError,
    parse_structural_metadata,
)
from riftlab.ingestion.infrastructure.raw_store import (
    DurableRawMatchStore,
    RawStoreCorruptedError,
)
from riftlab.ingestion.infrastructure.riot_transport import AMERICAS_HOST, RawRiotResponse
from riftlab.shared.errors import (
    SourceAuthError,
    SourceError,
    SourceNotFoundError,
    SourceRateLimitedError,
)

EXPLORATION_RAW_DIRNAME = "raw-exploration"
CONFIRMATION_RAW_DIRNAME = "raw-confirmation"
FIRST_CC316B_ATTEMPT = 7
FETCH_SCOPES = {"exploration": "EXPLORATION_FETCH", "confirmation": "CONFIRMATION_FETCH"}
FREEZE_RELATIVE_PATH = "validation/sprint-3/CC-321/12-congelamento-cc316.md"
FREEZE_SHA256 = "4d2b488e42a87cb60e9cd046b31aa801a95013d3d521d39b01bb1fb8289e3494"
_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")


def verify_freeze(private_root: Path, *, expected_sha256: str = FREEZE_SHA256) -> None:
    """Fail closed unless the sealed CC-316 freeze document and its sidecar are unchanged."""
    path = private_root / FREEZE_RELATIVE_PATH
    try:
        raw = path.read_bytes()
        sidecar = path.with_suffix(path.suffix + ".sha256").read_text(encoding="ascii")
    except (OSError, UnicodeError) as exc:
        raise DiscoveryError("congelamento CC-316 ausente ou ilegível") from exc
    digest = hashlib.sha256(raw).hexdigest()
    if digest != expected_sha256 or sidecar != f"{digest}  {path.name}\n":
        raise DiscoveryError("congelamento CC-316 ou sidecar divergente do valor fixado")


def load_adopted_confirmation_ids(
    path: Path,
    *,
    expected_sha256: str = ADOPTED_PARTITION_SHA256,
    freeze_sha256: str = FREEZE_SHA256,
) -> tuple[str, ...]:
    """Return the confirmation-potential IDs only after the sealed freeze is verified."""
    verify_freeze(private_root_for(path), expected_sha256=freeze_sha256)
    # The pinned loader re-validates the whole artifact, confirmation list included.
    load_adopted_exploration_ids(path, expected_sha256=expected_sha256)
    partition = json.loads(path.read_bytes())["partition"]
    return tuple(partition["confirmationPotentialMatchIds"])


def load_exploration_fetch_authorization(
    path: Path,
    *,
    attempt_number: int,
    adopted_partition_sha256: str = ADOPTED_PARTITION_SHA256,
    amendment_sha256: str = AMENDMENT_SHA256,
    scope: str = FETCH_SCOPES["exploration"],
) -> int:
    """Verify the user's authorization for one attempt and scope; return its request cap."""

    def require(condition: bool, message: str) -> None:
        if not condition:
            raise DiscoveryError(f"autorização CC-316-B inválida: {message}")

    private_root = private_root_for(path)
    raw, _digest = read_private_with_sidecar(path, "autorização")
    try:
        state = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise DiscoveryError("autorização CC-316-B inválida: JSON ilegível") from exc
    require(isinstance(state, dict), "raiz deve ser objeto")
    document = cast(dict[str, object], state)
    require(
        set(document)
        == {
            "schemaVersion",
            "protocol",
            "scope",
            "attemptNumber",
            "adoptedPartitionSha256",
            "amendmentSha256",
            "maxRequests",
            "decisionRecord",
        },
        "campos divergentes",
    )
    require(
        document["schemaVersion"] == 1
        and document["protocol"] == "CC-316-B"
        and scope in FETCH_SCOPES.values()
        and document["scope"] == scope,
        "versão, protocolo ou escopo",
    )
    attempt = document["attemptNumber"]
    require(
        isinstance(attempt, int)
        and not isinstance(attempt, bool)
        and attempt >= FIRST_CC316B_ATTEMPT
        and attempt == attempt_number,
        "tentativa autorizada diverge da solicitada",
    )
    require(
        bool(_SHA256_HEX.fullmatch(adopted_partition_sha256))
        and document["adoptedPartitionSha256"] == adopted_partition_sha256,
        "partição adotada diverge do valor fixado",
    )
    require(document["amendmentSha256"] == amendment_sha256, "emenda diverge do valor fixado")
    max_requests = document["maxRequests"]
    require(
        isinstance(max_requests, int)
        and not isinstance(max_requests, bool)
        and 1 <= max_requests <= MAX_REQUESTS,
        "teto de requisições fora do intervalo",
    )
    record = document["decisionRecord"]
    require(isinstance(record, dict) and set(record) == {"path", "sha256"}, "registro da decisão")
    record_map = cast(dict[str, object], record)
    record_relative = record_map["path"]
    record_sha256 = record_map["sha256"]
    require(
        isinstance(record_relative, str)
        and not Path(record_relative).is_absolute()
        and ".." not in Path(record_relative).parts
        and isinstance(record_sha256, str)
        and bool(_SHA256_HEX.fullmatch(record_sha256)),
        "caminho ou hash do registro da decisão",
    )
    record_path = private_root / cast(str, record_relative)
    try:
        record_bytes = record_path.read_bytes()
    except OSError as exc:
        raise DiscoveryError("autorização CC-316-B inválida: registro da decisão ausente") from exc
    require(
        hashlib.sha256(record_bytes).hexdigest() == record_sha256,
        "registro da decisão alterado",
    )
    return cast(int, max_requests)


def connectivity_preflight(
    host: str = AMERICAS_HOST,
    port: int = 443,
    *,
    timeout_seconds: float = 5.0,
    resolver: Callable[[str, int, int, int], Sequence[tuple[Any, ...]]] = socket.getaddrinfo,
    connector: Callable[[tuple[str, int], float], socket.socket] = socket.create_connection,
) -> None:
    """Resolve and open a TCP connection without TLS, API key or HTTP request."""
    outcome: dict[str, object] = {}

    def resolve() -> None:
        try:
            outcome["addresses"] = resolver(host, port, 0, socket.SOCK_STREAM)
        except OSError as exc:
            outcome["error"] = type(exc).__name__

    # A sandbox without network can hang in getaddrinfo; a daemon thread never
    # blocks interpreter exit the way asyncio's default executor did.
    worker = threading.Thread(target=resolve, name="cc316-dns-preflight", daemon=True)
    worker.start()
    worker.join(timeout_seconds)
    if worker.is_alive():
        raise DiscoveryError(
            "preflight de rede: DNS sem resposta (provável sandbox sem rede); "
            "nenhuma requisição Riot foi feita"
        )
    addresses = cast(Sequence[tuple[Any, ...]], outcome.get("addresses") or ())
    if "error" in outcome or not addresses:
        raise DiscoveryError(
            "preflight de rede: DNS indisponível; nenhuma requisição Riot foi feita"
        )
    socket_address = addresses[0][4]
    try:
        connection = connector((str(socket_address[0]), int(socket_address[1])), timeout_seconds)
    except OSError as exc:
        raise DiscoveryError(
            "preflight de rede: conexão TCP falhou; nenhuma requisição Riot foi feita"
        ) from exc
    connection.close()


class ExplorationTransport(Protocol):
    @property
    def requests_made(self) -> int: ...

    def set_request_observer(self, observer: Callable[[str], int]) -> None: ...

    def set_request_result_observer(
        self,
        observer: Callable[[int, str, int | None, float, str | None, str | None], None],
    ) -> None: ...

    def set_local_budget_observer(self, observer: Callable[[str, str], None]) -> None: ...

    def set_request_limit(self, limit: int) -> None: ...

    def set_total_time_limit(self, seconds: float) -> None: ...

    async def fetch_match(self, match_id: str) -> RawRiotResponse: ...

    async def fetch_timeline(self, match_id: str) -> RawRiotResponse: ...


@dataclass(frozen=True, slots=True)
class ExplorationFetchSummary:
    partition: str
    status: str
    match_count: int
    pairs_fetched: int
    pairs_already_present: int
    match_not_found: int
    timeline_not_found: int
    identity_or_structure_mismatch: int
    requests: int
    elapsed_seconds: float
    report_path: str
    report_sha256: str


def exploration_attempt_paths(
    output_dir: Path, attempt_number: int, partition: str = "exploration"
) -> dict[str, Path]:
    """Per-attempt files share the CC-320 ledger names so history stays cumulative."""
    if partition not in FETCH_SCOPES:
        raise DiscoveryError("partição CC-316-B fora da allowlist")
    budget = output_dir / f"request-budget-{attempt_number:02d}.json"
    raw_dirname = (
        EXPLORATION_RAW_DIRNAME if partition == "exploration" else CONFIRMATION_RAW_DIRNAME
    )
    return {
        "adopted": output_dir / ADOPTED_PARTITION_FILENAME,
        "budget": budget,
        "lifecycle": process_lifecycle_path(budget, attempt_number),
        "marker": output_dir / f".cc316-discovery-consumed-{attempt_number:02d}",
        "raw": output_dir / raw_dirname,
        "report": output_dir / f"{partition}-fetch-attempt-{attempt_number:02d}.json",
    }


def _structure_matches(body: bytes, match_id: str, secret: str) -> bool:
    try:
        record = parse_structural_metadata(
            body, requested_match_id=match_id, subject_hash_secret=secret
        )
    except StructuralMetadataError:
        return False
    return (record.patch, record.queue_id, record.map_id, record.game_complete) == (
        TARGET_PATCH,
        TARGET_QUEUE,
        TARGET_MAP,
        True,
    )


def _timeline_identity_matches(body: bytes, match_id: str) -> bool:
    try:
        document = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return False
    metadata = document.get("metadata") if isinstance(document, dict) else None
    return isinstance(metadata, dict) and metadata.get("matchId") == match_id


async def run_cc316_exploration_fetch(**kwargs: Any) -> ExplorationFetchSummary:
    """Fetch the 20 exploration pairs; see `run_cc316_partition_fetch`."""
    return await run_cc316_partition_fetch(partition="exploration", **kwargs)


async def run_cc316_partition_fetch(
    *,
    partition: str,
    transport: ExplorationTransport,
    output_dir: Path,
    attempt_number: int,
    authorized_max_requests: int,
    adopted_partition_sha256: str = ADOPTED_PARTITION_SHA256,
    freeze_sha256: str = FREEZE_SHA256,
    clock: Callable[[], float] = time.monotonic,
    process_started_monotonic_ns: int | None = None,
    boot_identifier: str | None = None,
    clock_ns: Callable[[], int] = time.monotonic_ns,
    temporal_baseline_path: Path | None = None,
) -> ExplorationFetchSummary:
    """Fetch and store raw match+timeline pairs for one partition's IDs only.

    Every HTTP attempt is reserved durably before it is sent. Pairs already in
    the private raw store cost zero requests, so an authorized later attempt
    resumes without refetching. Confirmation IDs require the sealed freeze.
    """
    if partition not in FETCH_SCOPES:
        raise DiscoveryError("partição CC-316-B fora da allowlist")
    if (
        isinstance(attempt_number, bool)
        or not isinstance(attempt_number, int)
        or attempt_number < FIRST_CC316B_ATTEMPT
    ):
        raise DiscoveryError("tentativa CC-316-B deve ser a 7 ou posterior")
    if (
        isinstance(authorized_max_requests, bool)
        or not isinstance(authorized_max_requests, int)
        or not 1 <= authorized_max_requests <= MAX_REQUESTS
    ):
        raise DiscoveryError("teto autorizado de requisições inválido")
    if transport.requests_made:
        raise DiscoveryError(
            "transporte já emitiu requisições; nova tentativa exige instância limpa"
        )
    if output_dir.name != "cc316" or not any(part == ".private" for part in output_dir.parts):
        raise DiscoveryError("saída deve ser o diretório privado cc316")
    try:
        output_mode = stat.S_IMODE(output_dir.stat().st_mode)
    except OSError as exc:
        raise DiscoveryError("diretório privado cc316 ausente") from exc
    if output_mode & 0o077:
        raise DiscoveryError("diretório cc316 deve usar modo privado")

    paths = exploration_attempt_paths(output_dir, attempt_number, partition)
    if partition == "exploration":
        match_ids = load_adopted_exploration_ids(
            paths["adopted"], expected_sha256=adopted_partition_sha256
        )
    else:
        match_ids = load_adopted_confirmation_ids(
            paths["adopted"], expected_sha256=adopted_partition_sha256, freeze_sha256=freeze_sha256
        )
    started = clock()
    process_started = (
        clock_ns() if process_started_monotonic_ns is None else process_started_monotonic_ns
    )
    if (
        isinstance(process_started, bool)
        or not isinstance(process_started, int)
        or process_started < 0
    ):
        raise DiscoveryError("início monotônico do processo inválido")
    prior_requests, prior_elapsed_ns = validate_prior_budget_history(
        paths["budget"],
        attempt_number,
        boot_identifier=boot_identifier or current_boot_id(),
        current_process_started_monotonic_ns=process_started,
        temporal_baseline_path=temporal_baseline_path,
    )
    request_limit = min(MAX_REQUESTS - prior_requests, authorized_max_requests)
    if any(paths[name].exists() for name in ("report", "marker", "budget")):
        raise DiscoveryError("esta tentativa já foi iniciada ou concluída; revisão necessária")

    raw_directory = paths["raw"]
    try:
        raw_directory.mkdir(mode=0o700, exist_ok=True)
        raw_directory.chmod(0o700)
    except OSError as exc:
        raise DiscoveryError("armazenamento bruto privado indisponível") from exc
    store = DurableRawMatchStore(raw_directory)
    try:
        pending = sum(
            (store.read_if_valid(match_id, "match") is None)
            + (store.read_if_valid(match_id, "timeline") is None)
            for match_id in match_ids
        )
    except RawStoreCorruptedError as exc:
        raise DiscoveryError(f"armazenamento bruto da partição {partition} corrompido") from exc
    if pending == 0:
        raise DiscoveryError(f"partição {partition} já completa no armazenamento privado")
    if request_limit < pending:
        raise DiscoveryError(
            f"orçamento insuficiente: {pending} pedidos pendentes e {max(request_limit, 0)} "
            "disponíveis sob o teto cumulativo e a autorização"
        )
    elapsed_before_ns = clock_ns() - process_started
    if elapsed_before_ns < 0:
        raise DiscoveryError("relógio monotônico retrocedeu dentro da mesma execução")
    remaining_time_ns = (
        _MAX_DURATION_NS
        - prior_elapsed_ns
        - elapsed_before_ns
        - _ASYNCIO_EXECUTOR_SHUTDOWN_RESERVE_NS
    )
    if remaining_time_ns <= 0:
        raise DiscoveryError("orçamento cumulativo de tempo insuficiente para a tentativa")

    lock_handle = (output_dir / ".cc316-discovery.lock").open("a+")
    try:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        lock_handle.close()
        raise DiscoveryError("há outra execução CC-316 em andamento") from exc
    try:
        marker_fd = os.open(paths["marker"], os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        lock_handle.close()
        raise DiscoveryError("tentativa já iniciada; revisão necessária") from exc
    try:
        os.write(marker_fd, datetime.now(UTC).isoformat().encode("ascii"))
        os.fsync(marker_fd)
    finally:
        os.close(marker_fd)

    # Structural recheck only; ephemeral HMACs are discarded with the secret.
    recheck_secret = secrets.token_hex(32)
    counts: Counter[str] = Counter()
    results: dict[str, dict[str, object]] = {}
    try:
        with BudgetJournal(paths["budget"], limit=request_limit) as journal:
            transport.set_request_limit(request_limit)
            transport.set_total_time_limit(remaining_time_ns / 1_000_000_000)
            transport.set_request_observer(journal.reserve)
            transport.set_request_result_observer(journal.record_result)
            transport.set_local_budget_observer(journal.record_local_budget)
            try:

                def ensure_time_budget() -> None:
                    elapsed_process_ns = clock_ns() - process_started
                    if (
                        elapsed_process_ns < 0
                        or prior_elapsed_ns
                        + elapsed_process_ns
                        + _ASYNCIO_EXECUTOR_SHUTDOWN_RESERVE_NS
                        >= _MAX_DURATION_NS
                    ):
                        raise DiscoveryError("teto de 60 minutos alcançado")

                for match_id in match_ids:
                    entry: dict[str, object] = {
                        "status": "",
                        "matchSha256": None,
                        "timelineSha256": None,
                    }
                    results[match_id] = entry
                    fetched = False
                    match_body = store.read_if_valid(match_id, "match")
                    if match_body is None:
                        ensure_time_budget()
                        try:
                            match_response = await transport.fetch_match(match_id)
                        except SourceNotFoundError:
                            counts["MATCH_NOT_FOUND"] += 1
                            entry["status"] = "MATCH_NOT_FOUND"
                            continue
                        if not _structure_matches(match_response.body, match_id, recheck_secret):
                            counts["IDENTITY_OR_STRUCTURE_MISMATCH"] += 1
                            entry["status"] = "IDENTITY_OR_STRUCTURE_MISMATCH"
                            continue
                        store.store(match_id, "match", match_response.body, match_response.sha256)
                        match_body = match_response.body
                        fetched = True
                    elif not _structure_matches(match_body, match_id, recheck_secret):
                        counts["IDENTITY_OR_STRUCTURE_MISMATCH"] += 1
                        entry["status"] = "IDENTITY_OR_STRUCTURE_MISMATCH"
                        continue
                    entry["matchSha256"] = hashlib.sha256(match_body).hexdigest()

                    timeline_body = store.read_if_valid(match_id, "timeline")
                    if timeline_body is None:
                        ensure_time_budget()
                        try:
                            timeline_response = await transport.fetch_timeline(match_id)
                        except SourceNotFoundError:
                            counts["TIMELINE_NOT_FOUND"] += 1
                            entry["status"] = "TIMELINE_NOT_FOUND"
                            continue
                        if not _timeline_identity_matches(timeline_response.body, match_id):
                            counts["IDENTITY_OR_STRUCTURE_MISMATCH"] += 1
                            entry["status"] = "IDENTITY_OR_STRUCTURE_MISMATCH"
                            continue
                        store.store(
                            match_id,
                            "timeline",
                            timeline_response.body,
                            timeline_response.sha256,
                        )
                        timeline_body = timeline_response.body
                        fetched = True
                    entry["timelineSha256"] = hashlib.sha256(timeline_body).hexdigest()
                    entry["status"] = "FETCHED" if fetched else "ALREADY_PRESENT"
                    counts["PAIR_FETCHED" if fetched else "PAIR_ALREADY_PRESENT"] += 1

                complete = counts["PAIR_FETCHED"] + counts["PAIR_ALREADY_PRESENT"] == len(match_ids)
                requests_used = journal.requests_reserved
                elapsed_seconds = round(clock() - started, 3)
                report: dict[str, object] = {
                    "schemaVersion": 1,
                    "protocol": "CC-316-B",
                    "phase": FETCH_SCOPES[partition],
                    "attemptNumber": attempt_number,
                    "adoptedPartitionSha256": adopted_partition_sha256,
                    "status": "COMPLETED" if complete else "PARTIAL",
                    f"{partition}MatchIds": list(match_ids),
                    "resultByMatchId": results,
                    "counts": dict(sorted(counts.items())),
                    "requests": requests_used,
                    "elapsedSeconds": elapsed_seconds,
                    "privacy": {
                        "confirmationFetched": partition == "confirmation",
                        "databaseAccessed": False,
                        "eventsProcessed": False,
                        "participantIdentifiersInReport": False,
                        "rawStoredOnlyUnderPrivateDirectory": True,
                    },
                }
                report_sha256 = _atomic_write_once(paths["report"], canonical_json(report))
                journal.finish(status="COMPLETED")
                return ExplorationFetchSummary(
                    partition=partition,
                    status="COMPLETED" if complete else "PARTIAL",
                    match_count=len(match_ids),
                    pairs_fetched=counts["PAIR_FETCHED"],
                    pairs_already_present=counts["PAIR_ALREADY_PRESENT"],
                    match_not_found=counts["MATCH_NOT_FOUND"],
                    timeline_not_found=counts["TIMELINE_NOT_FOUND"],
                    identity_or_structure_mismatch=counts["IDENTITY_OR_STRUCTURE_MISMATCH"],
                    requests=requests_used,
                    elapsed_seconds=elapsed_seconds,
                    report_path=str(paths["report"]),
                    report_sha256=report_sha256,
                )
            except SourceAuthError:
                journal.finish(status="STOPPED_AUTH")
                raise
            except SourceRateLimitedError:
                journal.finish(status="STOPPED_RATE_OR_BUDGET")
                raise
            except SourceError as exc:
                journal.finish(status="INTERRUPTED", failure_code=exc.code)
                raise
            except Exception:
                journal.finish(status="INTERRUPTED")
                raise
    finally:
        lock_handle.close()
