"""One-shot, metadata-only CC-316 discovery and private partition snapshot.

This path is intentionally separate from CC-206's raw+timeline collector.
Participant PUUIDs exist only in process memory and are never part of reports.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
import re
import stat
import time
import uuid
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import NoReturn, Protocol, TextIO, cast

from riftlab.collection.metadata_partition import (
    PartitionResult,
    StructuralMetadata,
    StructuralMetadataError,
    build_partition,
    parse_structural_metadata,
)
from riftlab.collection.seeds import Seed
from riftlab.shared.errors import (
    SourceAuthError,
    SourceError,
    SourceNotFoundError,
    SourceRateLimitedError,
)

TARGET_PATCH = "16.19"
TARGET_QUEUE = 420
TARGET_MAP = 11
MAX_REQUESTS = 1_000
MAX_CANDIDATES = 320
MAX_DURATION_SECONDS = 3_600.0
_MAX_DURATION_NS = int(MAX_DURATION_SECONDS * 1_000_000_000)
_ASYNCIO_EXECUTOR_SHUTDOWN_RESERVE_NS = 300_000_000_000
MAX_MATCHES_PER_PLAYER = 4
DISCOVERY_DEPTH = 2
_DIAGNOSTIC_REQUEST_CLASSES = frozenset(
    {"account", "match-list", "match-structural", "match", "timeline"}
)
_DIAGNOSTIC_RESULTS = frozenset(
    {
        "PENDING",
        "HTTP_ERROR",
        "HTTP_5XX",
        "HTTP_UNEXPECTED",
        "HTTP_200",
        "HTTP_AUTH",
        "HTTP_NOT_FOUND",
        "HTTP_429",
        "LOCAL_TIME_BUDGET",
        "LOCAL_REQUEST_BUDGET",
    }
)
_DIAGNOSTIC_EXCEPTION_CLASSES = frozenset(
    {
        "ConnectTimeout",
        "ReadTimeout",
        "WriteTimeout",
        "PoolTimeout",
        "ConnectError",
        "ReadError",
        "WriteError",
        "ProxyError",
        "RemoteProtocolError",
        "LocalProtocolError",
        "ProtocolError",
        "HTTPError",
    }
)
_DIAGNOSTIC_CAUSE_CATEGORIES = frozenset({"DNS", "SOCKET", "TLS", "PROXY", "UNKNOWN"})
_SNAPSHOT_MATCH_ID = re.compile(r"^BR1_[0-9]{1,20}$")
_SNAPSHOT_SOURCES = {"CONSENTED_SEED_MATCH_LIST": 0, "PARTICIPANT_MATCH_LIST": 1}
_SNAPSHOT_UNKNOWN_REASONS = frozenset({"MAX_CANDIDATES", "MAX_MATCHES_PER_PLAYER"})
_SNAPSHOT_EXCLUSION_REASONS = frozenset(
    {"OUT_OF_SCOPE", "MATCH_NOT_FOUND", "StructuralMetadataError"}
)
_SNAPSHOT_PRIVACY_FLAGS = {
    "participantPuuidsPersisted": False,
    "participantHmacsPersisted": False,
    "matchBodiesPersisted": False,
    "timelineFetched": False,
}
_TEMPORAL_BASELINE_SCHEMA = 1
_TEMPORAL_BASELINE_ID = "CC-320-TEMPORAL-OPTION-B-2026-10-02"
_TEMPORAL_BASELINE_AMENDMENT_PATH = (
    "validation/sprint-3/CC-320/21-temporal-budget-amendment-option-b-2026-10-02.md"
)
_TEMPORAL_BASELINE_AMENDMENT_SHA256 = (
    "dc8bf44f48c99890aa59eda6d93461acb10fe6d0ac7a213a6037371d684405ce"
)
_TEMPORAL_BASELINE_ATTEMPTS = [1, 2, 3, 4, 5, 6]
_TEMPORAL_BASELINE_ELAPSED_SECONDS = 1_671.911
_TEMPORAL_BASELINE_ELAPSED_NS = 1_671_911_000_000
_TEMPORAL_BASELINE_REQUESTS = 546
_TEMPORAL_BASELINE_RELATIVE_PATH = Path(
    ".private/validation/sprint-3/CC-320/22-temporal-budget-baseline-option-b-2026-10-02.json"
)
_TEMPORAL_BASELINE_LIMITATIONS = [
    "Intervalos observados em timestamps UTC de parede; nao monotônicos.",
    "Tentativas 1 e 5 encerram no Ctrl-C observado, sem alegar retorno natural.",
    "A convenção operacional aprovada não prova ausência de ajuste do relógio.",
]


def require_candidate_budget_resolved() -> NoReturn:
    """Keep CC-316 discovery closed: CC-316-B adopted attempt 4 and consumed the cap."""
    raise DiscoveryError(
        "descoberta CC-316 encerrada pela emenda CC-316-B: a partição da rodada 4 foi "
        "adotada e o teto de candidatos está consumido. Transporte bloqueado antes de "
        "qualquer I/O."
    )


def temporal_budget_baseline_path() -> Path:
    repository_root = Path(__file__).resolve().parents[4]
    return repository_root / _TEMPORAL_BASELINE_RELATIVE_PATH


def _temporal_budget_baseline_payload() -> dict[str, object]:
    return {
        "schemaVersion": _TEMPORAL_BASELINE_SCHEMA,
        "baselineId": _TEMPORAL_BASELINE_ID,
        "amendmentPath": _TEMPORAL_BASELINE_AMENDMENT_PATH,
        "amendmentSha256": _TEMPORAL_BASELINE_AMENDMENT_SHA256,
        "coveredAttempts": list(_TEMPORAL_BASELINE_ATTEMPTS),
        "operationalElapsedSeconds": _TEMPORAL_BASELINE_ELAPSED_SECONDS,
        "reservedRequests": _TEMPORAL_BASELINE_REQUESTS,
        "maxCumulativeSeconds": MAX_DURATION_SECONDS,
        "maxCumulativeRequests": MAX_REQUESTS,
        "accountingMethod": "SESSION_UTC_COMMAND_INTERVALS_UNTIL_RETURN_OR_CTRL_C",
        "limitations": list(_TEMPORAL_BASELINE_LIMITATIONS),
    }


def _private_regular_file(path: Path, field: str) -> os.stat_result:
    try:
        file_stat = path.lstat()
        parent_stat = path.parent.stat()
    except OSError as exc:
        raise DiscoveryError(f"baseline temporal ausente/inacessível ({field})") from exc
    if (
        not stat.S_ISREG(file_stat.st_mode)
        or stat.S_IMODE(file_stat.st_mode) != 0o600
        or stat.S_IMODE(parent_stat.st_mode) & 0o077
    ):
        raise DiscoveryError(
            f"baseline temporal inseguro ({field}); esperado diretório privado e modo 0600"
        )
    return file_stat


def create_operational_budget_baseline(path: Path | None = None) -> str:
    """Create the approved immutable UTC baseline and digest sidecar exactly once."""
    baseline_path = path or temporal_budget_baseline_path()
    if not any(part == ".private" for part in baseline_path.parts):
        raise DiscoveryError("baseline temporal deve permanecer sob .private")
    baseline_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if stat.S_IMODE(baseline_path.parent.stat().st_mode) & 0o077:
        raise DiscoveryError("diretório do baseline temporal deve usar modo privado")
    sidecar_path = baseline_path.with_suffix(baseline_path.suffix + ".sha256")
    if baseline_path.exists() or sidecar_path.exists():
        raise DiscoveryError("baseline temporal já existe; artefato imutável não será sobrescrito")
    content = (
        json.dumps(_temporal_budget_baseline_payload(), sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode()
    digest = hashlib.sha256(content).hexdigest()
    sidecar = f"{digest}  {baseline_path.name}\n".encode("ascii")
    for target, data in ((baseline_path, content), (sidecar_path, sidecar)):
        try:
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as stream:
                os.fchmod(stream.fileno(), 0o600)
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
        except FileExistsError as exc:
            raise DiscoveryError(
                "baseline temporal/sidecar já existe; não será sobrescrito"
            ) from exc
        except OSError as exc:
            raise DiscoveryError("falha ao persistir baseline temporal privado") from exc
    directory_fd = os.open(baseline_path.parent, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    return digest


def load_operational_budget_baseline(path: Path | None = None) -> tuple[int, int]:
    """Verify the immutable option-B baseline; return reserved requests and elapsed ns."""
    baseline_path = path or temporal_budget_baseline_path()
    if not any(part == ".private" for part in baseline_path.parts):
        raise DiscoveryError("baseline temporal deve permanecer sob .private")
    _private_regular_file(baseline_path, "JSON")
    sidecar_path = baseline_path.with_suffix(baseline_path.suffix + ".sha256")
    _private_regular_file(sidecar_path, "sidecar SHA-256")
    try:
        raw = baseline_path.read_bytes()
        sidecar = sidecar_path.read_text(encoding="ascii")
        state = json.loads(raw)
    except (OSError, json.JSONDecodeError, UnicodeError) as exc:
        raise DiscoveryError("baseline temporal ilegível ou inválido") from exc
    digest = hashlib.sha256(raw).hexdigest()
    if sidecar != f"{digest}  {baseline_path.name}\n":
        raise DiscoveryError("hash/sidecar do baseline temporal divergente")
    canonical = (
        json.dumps(_temporal_budget_baseline_payload(), sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode()
    if raw != canonical or state != _temporal_budget_baseline_payload():
        raise DiscoveryError("schema, conteúdo ou aprovação do baseline temporal inválidos")
    return _TEMPORAL_BASELINE_REQUESTS, _TEMPORAL_BASELINE_ELAPSED_NS


class StructuralTransport(Protocol):
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

    async def resolve_account_puuid(self, game_name: str, tag_line: str) -> str: ...

    async def list_match_ids_by_puuid(
        self,
        puuid: str,
        *,
        queue: int,
        start: int,
        count: int,
        start_time: int | None = None,
        end_time: int | None = None,
    ) -> list[str]: ...

    async def fetch_match_for_structural_projection(self, match_id: str) -> bytes: ...


class DiscoveryError(RuntimeError):
    """Safe, non-sensitive failure during CC-316 structural discovery."""


class BudgetJournal:
    """Durable per-request cap; interrupted invocations cannot silently restart."""

    def __init__(
        self, path: Path, *, limit: int = MAX_REQUESTS, binding: dict[str, object] | None = None
    ) -> None:
        self.path = path
        self.limit = limit
        self.binding = binding
        self._lock_handle: TextIO | None = None
        self._state: dict[str, object] = {}
        self._diagnostic_sequence = 0

    def __enter__(self) -> BudgetJournal:
        if not any(part == ".private" for part in self.path.parent.parts):
            raise DiscoveryError("jornal do orçamento deve ficar dentro de .private")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.parent.stat().st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            raise DiscoveryError("diretório do orçamento permite escrita por grupo ou outros")
        lock_path = self.path.parent / ".cc316-budget.lock"
        self._lock_handle = lock_path.open("a+")
        try:
            fcntl.flock(self._lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self._lock_handle.close()
            self._lock_handle = None
            raise DiscoveryError("há outra execução CC-316 em andamento") from exc
        if self.path.exists():
            self._lock_handle.close()
            self._lock_handle = None
            raise DiscoveryError("jornal de orçamento já existe; revisão manual necessária")
        self._state = {
            "schemaVersion": 4 if self.binding is not None else 3,
            "status": "IN_PROGRESS",
            "requestsReserved": 0,
            "startedAt": datetime.now(UTC).isoformat(),
            "diagnostics": [],
        }
        if self.binding is not None:
            self._state["campaignBinding"] = self.binding
        self._save()
        return self

    def reserve(self, request_class: str = "account") -> int:
        if request_class not in _DIAGNOSTIC_REQUEST_CLASSES:
            raise DiscoveryError("classe de request fora da allowlist diagnóstica")
        current = cast(int, self._state["requestsReserved"])
        if current >= self.limit:
            raise DiscoveryError("teto de requisições CC-316 alcançado")
        self._diagnostic_sequence += 1
        ordinal = current + 1
        self._state["requestsReserved"] = current + 1
        self._append_diagnostic(
            {
                "sequence": self._diagnostic_sequence,
                "ordinal": ordinal,
                "requestClass": request_class,
                "result": "PENDING",
                "statusCode": None,
                "durationMs": None,
                "exceptionClass": None,
                "causeCategory": None,
                "requestReserved": True,
            }
        )
        self._save()
        return ordinal

    def record_result(
        self,
        ordinal: int,
        result: str,
        status_code: int | None,
        duration_ms: float,
        exception_class: str | None,
        cause_category: str | None,
    ) -> None:
        if result not in _DIAGNOSTIC_RESULTS - {
            "PENDING",
            "LOCAL_TIME_BUDGET",
            "LOCAL_REQUEST_BUDGET",
        }:
            raise DiscoveryError("resultado fora da allowlist diagnóstica")
        if status_code is not None and (
            isinstance(status_code, bool)
            or not isinstance(status_code, int)
            or not 100 <= status_code <= 599
        ):
            raise DiscoveryError("status HTTP fora do formato permitido")
        if (
            isinstance(duration_ms, bool)
            or not isinstance(duration_ms, (int, float))
            or not math.isfinite(duration_ms)
            or duration_ms < 0
        ):
            raise DiscoveryError("duração monotônica fora do formato permitido")
        if exception_class is not None and exception_class not in _DIAGNOSTIC_EXCEPTION_CLASSES:
            raise DiscoveryError("classe de exceção fora da allowlist diagnóstica")
        if cause_category is not None and cause_category not in _DIAGNOSTIC_CAUSE_CATEGORIES:
            raise DiscoveryError("causa de exceção fora da allowlist diagnóstica")
        diagnostics = cast(list[dict[str, object]], self._state["diagnostics"])
        matching = [entry for entry in diagnostics if entry.get("ordinal") == ordinal]
        if len(matching) != 1 or matching[0].get("result") != "PENDING":
            raise DiscoveryError("reserva diagnóstica pendente ausente ou duplicada")
        if (result == "HTTP_ERROR") != (status_code is None):
            raise DiscoveryError("status HTTP inconsistente com o resultado")
        if (result == "HTTP_ERROR") != (exception_class is not None):
            raise DiscoveryError("classe de exceção inconsistente com o resultado")
        if (result == "HTTP_ERROR") != (cause_category is not None):
            raise DiscoveryError("causa de exceção inconsistente com o resultado")
        matching[0]["result"] = result
        matching[0]["statusCode"] = status_code
        matching[0]["durationMs"] = round(float(duration_ms), 3)
        matching[0]["exceptionClass"] = exception_class
        matching[0]["causeCategory"] = cause_category
        self._save()

    def record_local_budget(self, request_class: str, budget_kind: str) -> None:
        if request_class not in _DIAGNOSTIC_REQUEST_CLASSES:
            raise DiscoveryError("classe de request fora da allowlist diagnóstica")
        if budget_kind not in {"LOCAL_TIME_BUDGET", "LOCAL_REQUEST_BUDGET"}:
            raise DiscoveryError("categoria de orçamento fora da allowlist diagnóstica")
        self._diagnostic_sequence += 1
        self._append_diagnostic(
            {
                "sequence": self._diagnostic_sequence,
                "ordinal": None,
                "requestClass": request_class,
                "result": budget_kind,
                "statusCode": None,
                "durationMs": None,
                "exceptionClass": None,
                "causeCategory": None,
                "requestReserved": False,
            }
        )
        self._save()

    def _append_diagnostic(self, entry: dict[str, object]) -> None:
        diagnostics = cast(list[dict[str, object]], self._state["diagnostics"])
        diagnostics.append(entry)

    @property
    def requests_reserved(self) -> int:
        return cast(int, self._state["requestsReserved"])

    def finish(self, *, status: str, failure_code: str | None = None) -> None:
        self._state["status"] = status
        if failure_code is not None:
            self._state["failureCode"] = failure_code
        self._state["finishedAt"] = datetime.now(UTC).isoformat()
        self._save()

    def _save(self) -> None:
        temp = self.path.with_name(f"{self.path.name}.tmp-{os.getpid()}-{uuid.uuid4().hex}")
        try:
            with temp.open("x", encoding="utf-8") as stream:
                os.fchmod(stream.fileno(), 0o600)
                json.dump(self._state, stream, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, self.path)
            directory_fd = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError as exc:
            temp.unlink(missing_ok=True)
            raise DiscoveryError("não foi possível persistir o limite da coleta") from exc

    def __exit__(self, *_: object) -> None:
        if self._lock_handle is not None:
            self._lock_handle.close()
            self._lock_handle = None


def current_boot_id() -> str:
    """Read the local Linux boot identifier; absence means timing is unverifiable."""
    try:
        value = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
        return str(uuid.UUID(value))
    except (OSError, UnicodeError, ValueError, AttributeError) as exc:
        raise DiscoveryError("boot ID indisponível; orçamento temporal não verificável") from exc


def process_lifecycle_path(budget_path: Path, attempt_number: int) -> Path:
    if attempt_number == 1:
        return budget_path.with_name("process-lifecycle.json")
    return budget_path.with_name(f"process-lifecycle-{attempt_number:02d}.json")


class ProcessLifecycleJournal:
    """Durably record coroutine work and complete asyncio shutdown on one boot."""

    def __init__(
        self,
        path: Path,
        *,
        attempt_number: int = 1,
        boot_id: str | None = None,
        process_started_monotonic_ns: int | None = None,
        clock_ns: Callable[[], int] = time.monotonic_ns,
        binding: dict[str, object] | None = None,
    ) -> None:
        self.path = path
        self.attempt_number = attempt_number
        if (
            isinstance(attempt_number, bool)
            or not isinstance(attempt_number, int)
            or attempt_number < 1
        ):
            raise DiscoveryError("número de tentativa inválido no ciclo do processo")
        self.boot_id = str(uuid.UUID(boot_id)) if boot_id is not None else current_boot_id()
        self._process_started_monotonic_ns = process_started_monotonic_ns
        self._clock_ns = clock_ns
        self.binding = binding
        self._state: dict[str, object] = {}
        self._created = False

    @property
    def process_started_monotonic_ns(self) -> int:
        return cast(int, self._state["processStartedMonotonicNs"])

    def __enter__(self) -> ProcessLifecycleJournal:
        if not any(part == ".private" for part in self.path.parent.parts):
            raise DiscoveryError("diário de processo deve ficar dentro de .private")
        if self.path.parent.stat().st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            raise DiscoveryError("diretório do processo permite escrita por grupo ou outros")
        started = self._process_started_monotonic_ns
        if started is None:
            started = self._clock_ns()
        if isinstance(started, bool) or not isinstance(started, int) or started < 0:
            raise DiscoveryError("relógio monotônico inválido")
        self._state = {
            "schemaVersion": 1,
            "attemptNumber": self.attempt_number,
            "lifecycleStatus": "IN_PROGRESS",
            "runOutcome": "IN_PROGRESS",
            "bootId": self.boot_id,
            "processStartedMonotonicNs": started,
            "coroutineStartedMonotonicNs": None,
            "coroutineFinishedMonotonicNs": None,
            "processFinishedMonotonicNs": None,
            "processElapsedNs": None,
            "shutdownElapsedNs": None,
            "executorShutdownTimeoutNs": _ASYNCIO_EXECUTOR_SHUTDOWN_RESERVE_NS,
        }
        if self.binding is not None:
            self._state["campaignBinding"] = self.binding
        self._save(exclusive=True)
        self._created = True
        return self

    def mark_coroutine_started(self) -> None:
        self._set_monotonic_field("coroutineStartedMonotonicNs")

    def mark_coroutine_finished(self) -> None:
        self._set_monotonic_field("coroutineFinishedMonotonicNs")

    def _set_monotonic_field(self, field: str) -> None:
        if not self._created or self._state.get("lifecycleStatus") != "IN_PROGRESS":
            raise DiscoveryError("estado do diário de processo inválido")
        if self._state.get(field) is not None:
            raise DiscoveryError("marcador monotônico de processo duplicado")
        now = self._clock_ns()
        prior_fields = ["processStartedMonotonicNs"]
        if field == "coroutineFinishedMonotonicNs":
            prior_fields.append("coroutineStartedMonotonicNs")
        if (
            isinstance(now, bool)
            or not isinstance(now, int)
            or any(
                not isinstance(self._state.get(prior), int) or now < cast(int, self._state[prior])
                for prior in prior_fields
            )
        ):
            raise DiscoveryError("ordem do relógio monotônico inválida")
        self._state[field] = now
        self._save()

    def finish(self, *, run_outcome: str) -> None:
        if run_outcome not in {"COMPLETED", "INTERRUPTED"}:
            raise DiscoveryError("resultado do processo fora da allowlist")
        if not self._created or self._state.get("lifecycleStatus") != "IN_PROGRESS":
            raise DiscoveryError("estado do diário de processo inválido")
        finished = self._clock_ns()
        started = self._state.get("processStartedMonotonicNs")
        coroutine_started = self._state.get("coroutineStartedMonotonicNs")
        coroutine_finished = self._state.get("coroutineFinishedMonotonicNs")
        if (
            isinstance(finished, bool)
            or not isinstance(finished, int)
            or not isinstance(started, int)
            or not isinstance(coroutine_started, int)
            or not isinstance(coroutine_finished, int)
            or finished < coroutine_finished
            or coroutine_started < started
            or coroutine_finished < coroutine_started
        ):
            self._state["lifecycleStatus"] = "INCOMPLETE"
            self._state["runOutcome"] = run_outcome
            self._save()
            raise DiscoveryError("intervalo monotônico de processo incompleto")
        shutdown_elapsed = finished - coroutine_finished
        self._state["processFinishedMonotonicNs"] = finished
        self._state["processElapsedNs"] = finished - started
        self._state["shutdownElapsedNs"] = shutdown_elapsed
        self._state["runOutcome"] = run_outcome
        self._state["lifecycleStatus"] = (
            "SHUTDOWN_TIMEOUT"
            if shutdown_elapsed >= _ASYNCIO_EXECUTOR_SHUTDOWN_RESERVE_NS
            else "COMPLETE"
        )
        self._save()

    def _save(self, *, exclusive: bool = False) -> None:
        temp = self.path.with_name(f"{self.path.name}.tmp-{os.getpid()}-{uuid.uuid4().hex}")
        try:
            if exclusive:
                fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                stream = os.fdopen(fd, "w", encoding="utf-8")
            else:
                stream = temp.open("x", encoding="utf-8")
            with stream:
                os.fchmod(stream.fileno(), 0o600)
                json.dump(self._state, stream, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            if not exclusive:
                os.replace(temp, self.path)
            directory_fd = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except FileExistsError as exc:
            temp.unlink(missing_ok=True)
            raise DiscoveryError("diário do processo já existe; revisão necessária") from exc
        except OSError as exc:
            temp.unlink(missing_ok=True)
            raise DiscoveryError("não foi possível persistir o ciclo do processo") from exc


def validate_prior_budget_history(
    budget_path: Path,
    attempt_number: int,
    *,
    boot_identifier: str | None = None,
    current_process_started_monotonic_ns: int | None = None,
    temporal_baseline_path: Path | None = None,
) -> tuple[int, int]:
    """Return approved historical baseline plus comparable new process totals."""
    if (
        isinstance(attempt_number, bool)
        or not isinstance(attempt_number, int)
        or attempt_number < 1
    ):
        raise DiscoveryError("número de tentativa inválido")
    if attempt_number == 1:
        return 0, 0
    current_boot = boot_identifier or current_boot_id()
    try:
        current_boot = str(uuid.UUID(current_boot))
    except (AttributeError, TypeError, ValueError) as exc:
        raise DiscoveryError("boot ID atual inválido; orçamento temporal bloqueado") from exc

    first_prior_attempt = 1
    if attempt_number >= 7:
        prior_requests, prior_elapsed_ns = load_operational_budget_baseline(temporal_baseline_path)
        first_prior_attempt = 7
    else:
        prior_requests = 0
        prior_elapsed_ns = 0
    previous_process_end_ns = -1
    current_monotonic_ns = time.monotonic_ns()
    if current_process_started_monotonic_ns is not None and (
        isinstance(current_process_started_monotonic_ns, bool)
        or not isinstance(current_process_started_monotonic_ns, int)
        or current_process_started_monotonic_ns < 0
    ):
        raise DiscoveryError("início monotônico atual inválido; orçamento temporal bloqueado")
    for previous_attempt in range(first_prior_attempt, attempt_number):
        previous_marker = budget_path.parent / (
            ".cc316-discovery-consumed"
            if previous_attempt == 1
            else f".cc316-discovery-consumed-{previous_attempt:02d}"
        )
        previous_budget = budget_path.with_name(
            "request-budget.json"
            if previous_attempt == 1
            else f"request-budget-{previous_attempt:02d}.json"
        )
        previous_lifecycle = process_lifecycle_path(budget_path, previous_attempt)
        try:
            budget_state = json.loads(previous_budget.read_text(encoding="utf-8"))
            lifecycle_state = json.loads(previous_lifecycle.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeError) as exc:
            raise DiscoveryError(
                "histórico temporal legado/incompleto; continuidade exige decisão/emenda explícita"
            ) from exc

        requests = budget_state.get("requestsReserved") if isinstance(budget_state, dict) else None
        if (
            not previous_marker.exists()
            or not isinstance(budget_state, dict)
            or type(budget_state.get("schemaVersion")) is not int
            or budget_state.get("schemaVersion") != 3
            or budget_state.get("status") not in {"INTERRUPTED", "COMPLETED"}
            or isinstance(requests, bool)
            or not isinstance(requests, int)
            or requests < 0
            or requests > MAX_REQUESTS
            or not isinstance(lifecycle_state, dict)
            or type(lifecycle_state.get("schemaVersion")) is not int
            or lifecycle_state.get("schemaVersion") != 1
            or type(lifecycle_state.get("attemptNumber")) is not int
            or lifecycle_state.get("attemptNumber") != previous_attempt
            or lifecycle_state.get("lifecycleStatus") not in {"COMPLETE", "SHUTDOWN_TIMEOUT"}
            or lifecycle_state.get("runOutcome") not in {"COMPLETED", "INTERRUPTED"}
            or lifecycle_state.get("runOutcome")
            != ("COMPLETED" if budget_state.get("status") == "COMPLETED" else "INTERRUPTED")
            or lifecycle_state.get("bootId") != current_boot
            or lifecycle_state.get("executorShutdownTimeoutNs")
            != _ASYNCIO_EXECUTOR_SHUTDOWN_RESERVE_NS
        ):
            raise DiscoveryError(
                "histórico temporal legado/incomparável; continuidade exige "
                "decisão/emenda explícita"
            )

        fields = (
            "processStartedMonotonicNs",
            "coroutineStartedMonotonicNs",
            "coroutineFinishedMonotonicNs",
            "processFinishedMonotonicNs",
            "processElapsedNs",
            "shutdownElapsedNs",
        )
        values = [lifecycle_state.get(field) for field in fields]
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in values
        ):
            raise DiscoveryError(
                "histórico temporal sem intervalo monotônico completo; continuidade exige "
                "decisão/emenda explícita"
            )
        started, coroutine_started, coroutine_finished, finished, elapsed, shutdown = cast(
            list[int], values
        )
        if (
            not started <= coroutine_started <= coroutine_finished <= finished
            or started < previous_process_end_ns
            or finished > current_monotonic_ns
            or (
                current_process_started_monotonic_ns is not None
                and finished > current_process_started_monotonic_ns
            )
            or elapsed != finished - started
            or shutdown != finished - coroutine_finished
            or lifecycle_state.get("lifecycleStatus")
            != (
                "SHUTDOWN_TIMEOUT"
                if shutdown >= _ASYNCIO_EXECUTOR_SHUTDOWN_RESERVE_NS
                else "COMPLETE"
            )
        ):
            raise DiscoveryError(
                "histórico temporal monotônico incoerente; continuidade exige "
                "decisão/emenda explícita"
            )
        prior_requests += requests
        prior_elapsed_ns += elapsed
        previous_process_end_ns = finished

    if prior_requests > MAX_REQUESTS or prior_elapsed_ns >= _MAX_DURATION_NS:
        return prior_requests, prior_elapsed_ns
    return prior_requests, prior_elapsed_ns


@dataclass(frozen=True, slots=True)
class DiscoverySummary:
    status: str
    seed_count: int
    players_queried: int
    candidates_seen: int
    metadata_fetched: int
    structural_exclusions: dict[str, int]
    exploration_count: int
    confirmation_potential_count: int
    overlap_exclusions: int
    requests: int
    elapsed_seconds: float
    artifact_path: str
    artifact_sha256: str


@dataclass(frozen=True, slots=True)
class DiscoveryResult:
    summary: DiscoverySummary
    partition: PartitionResult


def _atomic_write_once(path: Path, content: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(content).hexdigest()
    temp = path.with_name(f"{path.name}.tmp-{os.getpid()}-{uuid.uuid4().hex}")
    with temp.open("xb") as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        os.link(temp, path)
    except FileExistsError as exc:
        temp.unlink(missing_ok=True)
        raise DiscoveryError("snapshot já existe; não será sobrescrito") from exc
    finally:
        temp.unlink(missing_ok=True)

    sidecar = path.with_suffix(path.suffix + ".sha256")
    sidecar_content = f"{digest}  {path.name}\n".encode()
    sidecar_temp = sidecar.with_name(f"{sidecar.name}.tmp-{os.getpid()}-{uuid.uuid4().hex}")
    with sidecar_temp.open("xb") as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(sidecar_content)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        os.link(sidecar_temp, sidecar)
    except FileExistsError as exc:
        sidecar_temp.unlink(missing_ok=True)
        raise DiscoveryError("sidecar já existe; revisão manual necessária") from exc
    finally:
        sidecar_temp.unlink(missing_ok=True)

    directory_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    return digest


def validate_snapshot_contract(artifact: object) -> None:
    """Fail closed unless a newly assembled CC-316 snapshot is self-consistent."""

    def require(condition: bool, message: str) -> None:
        if not condition:
            raise DiscoveryError(f"contrato do snapshot inválido: {message}")

    def mapping(value: object, field: str) -> dict[str, object]:
        require(isinstance(value, dict), f"{field} deve ser objeto")
        return cast(dict[str, object], value)

    def identifiers(value: object, field: str) -> list[str]:
        require(isinstance(value, list), f"{field} deve ser lista")
        result = cast(list[object], value)
        require(all(isinstance(item, str) for item in result), f"{field} contém ID inválido")
        ids = cast(list[str], result)
        require(len(ids) == len(set(ids)), f"{field} contém duplicatas")
        require(all(_SNAPSHOT_MATCH_ID.fullmatch(item) for item in ids), f"{field} fora do escopo")
        return ids

    def exact_count(value: object, expected: int, field: str) -> None:
        require(
            isinstance(value, int) and not isinstance(value, bool) and value == expected,
            f"{field} divergente",
        )

    require(isinstance(artifact, dict), "raiz deve ser objeto")
    root = cast(dict[str, object], artifact)
    require(
        set(root)
        == {
            "schemaVersion",
            "protocol",
            "createdAt",
            "status",
            "target",
            "discovery",
            "partition",
            "privacy",
            "requests",
            "elapsedSeconds",
        },
        "campos de raiz divergentes",
    )
    require(root.get("schemaVersion") == 1 and root.get("protocol") == "CC-316-A", "versão")
    require(isinstance(root.get("createdAt"), str), "createdAt inválido")
    requests = root.get("requests")
    elapsed_seconds = root.get("elapsedSeconds")
    require(
        isinstance(requests, int)
        and not isinstance(requests, bool)
        and 0 <= requests <= MAX_REQUESTS,
        "contagem de requests fora do orçamento",
    )
    require(
        isinstance(elapsed_seconds, (int, float))
        and not isinstance(elapsed_seconds, bool)
        and math.isfinite(elapsed_seconds)
        and 0 <= elapsed_seconds <= MAX_DURATION_SECONDS,
        "duração fora do orçamento",
    )
    require(root.get("privacy") == _SNAPSHOT_PRIVACY_FLAGS, "flags de privacidade")
    target = mapping(root.get("target"), "target")
    require(
        target
        == {
            "patch": TARGET_PATCH,
            "queueId": TARGET_QUEUE,
            "mapId": TARGET_MAP,
            "gameComplete": True,
        },
        "target fora do protocolo",
    )
    discovery = mapping(root.get("discovery"), "discovery")
    partition = mapping(root.get("partition"), "partition")
    require(
        set(discovery)
        == {
            "source",
            "maximumParticipantHops",
            "maximumMatchesPerPlayer",
            "uniqueCandidateCount",
            "metadataRequestAttemptCount",
            "structuralMetadataValidCount",
            "stopReason",
            "candidateProvenanceByMatchId",
            "metadataRequestAttemptedMatchIds",
            "structuralMetadataValidMatchIds",
            "metadataNotAttemptedMatchIds",
            "metadataUnknownReasonByMatchId",
        },
        "campos de discovery divergentes",
    )
    require(
        set(partition)
        == {
            "hashRule",
            "partitionDigestByMatchId",
            "candidateMatchIds",
            "explorationMatchIds",
            "confirmationPotentialMatchIds",
            "overlapExcludedMatchIds",
            "usable",
            "structuralExcludedMatchIds",
            "exclusionCounts",
        },
        "campos de partition divergentes",
    )
    require(
        discovery.get("source") == "CONSENTED_SEEDS_PLUS_PARTICIPANTS"
        and discovery.get("maximumParticipantHops") == DISCOVERY_DEPTH
        and discovery.get("maximumMatchesPerPlayer") == MAX_MATCHES_PER_PLAYER,
        "limites/fonte de discovery divergentes",
    )
    candidates = identifiers(partition.get("candidateMatchIds"), "candidateMatchIds")
    require(candidates == sorted(candidates), "candidateMatchIds não está em ordem canônica")
    candidate_set = set(candidates)
    digests = mapping(partition.get("partitionDigestByMatchId"), "partitionDigestByMatchId")
    provenance = mapping(
        discovery.get("candidateProvenanceByMatchId"), "candidateProvenanceByMatchId"
    )
    require(
        set(digests) == candidate_set and set(provenance) == candidate_set,
        "mapas de candidato divergentes",
    )
    for match_id in candidates:
        expected_digest = hashlib.sha256(f"CC-316-partition-v1|{match_id}".encode()).hexdigest()
        require(digests[match_id] == expected_digest, "digest de partição divergente")
        source_record = mapping(provenance[match_id], f"proveniência {match_id}")
        require(set(source_record) == {"depth", "source"}, "campos extras na proveniência")
        source = source_record.get("source")
        depth = source_record.get("depth")
        require(isinstance(source, str) and source in _SNAPSHOT_SOURCES, "fonte fora da allowlist")
        require(
            isinstance(depth, int)
            and not isinstance(depth, bool)
            and (
                depth == _SNAPSHOT_SOURCES[source]
                if source == "CONSENTED_SEED_MATCH_LIST"
                else 1 <= depth <= DISCOVERY_DEPTH
            ),
            "fonte/profundidade incoerentes",
        )

    attempted = identifiers(
        discovery.get("metadataRequestAttemptedMatchIds"), "metadataRequestAttemptedMatchIds"
    )
    valid = identifiers(
        discovery.get("structuralMetadataValidMatchIds"), "structuralMetadataValidMatchIds"
    )
    unknown = identifiers(
        discovery.get("metadataNotAttemptedMatchIds"), "metadataNotAttemptedMatchIds"
    )
    require(
        attempted == sorted(attempted) and valid == sorted(valid) and unknown == sorted(unknown),
        "listas de discovery não estão em ordem canônica",
    )
    require(set(attempted).isdisjoint(unknown), "IDs tentados e não tentados se sobrepõem")
    require(len(attempted) <= MAX_CANDIDATES, "teto de metadados tentados excedido")
    require(
        set(attempted) | set(unknown) == candidate_set,
        "tentados/não tentados não cobrem candidatos",
    )
    require(set(valid) <= set(attempted), "metadata válido não foi tentado")
    failed = set(attempted) - set(valid)
    unknown_reasons = mapping(
        discovery.get("metadataUnknownReasonByMatchId"), "metadataUnknownReasonByMatchId"
    )
    require(set(unknown_reasons) == set(unknown), "motivos de desconhecidos incompletos")
    require(
        all(
            isinstance(reason, str) and reason in _SNAPSHOT_UNKNOWN_REASONS
            for reason in unknown_reasons.values()
        ),
        "motivo desconhecido fora da allowlist",
    )
    exact_count(discovery.get("uniqueCandidateCount"), len(candidates), "contagem de candidatos")
    exact_count(
        discovery.get("metadataRequestAttemptCount"), len(attempted), "contagem de tentativas"
    )
    exact_count(
        discovery.get("structuralMetadataValidCount"), len(valid), "contagem de metadados válidos"
    )

    exclusions = mapping(partition.get("structuralExcludedMatchIds"), "structuralExcludedMatchIds")
    require(set(exclusions) <= candidate_set, "exclusão fora do universo candidato")
    require(
        all(isinstance(reason, str) for reason in exclusions.values()),
        "motivo de exclusão inválido",
    )
    require(
        set(exclusions).isdisjoint(unknown), "desconhecido classificado como exclusão estrutural"
    )
    out_of_scope = {match_id for match_id, reason in exclusions.items() if reason == "OUT_OF_SCOPE"}
    failed_reasons = {
        match_id: reason for match_id, reason in exclusions.items() if reason != "OUT_OF_SCOPE"
    }
    require(out_of_scope <= set(valid), "OUT_OF_SCOPE sem metadado estrutural válido")
    require(set(failed_reasons) == failed, "falhas tentadas sem motivo ou com IDs divergentes")
    require(
        all(
            isinstance(reason, str) and reason in _SNAPSHOT_EXCLUSION_REASONS - {"OUT_OF_SCOPE"}
            for reason in failed_reasons.values()
        ),
        "causa de exclusão fora da allowlist",
    )
    counts = mapping(partition.get("exclusionCounts"), "exclusionCounts")
    require(
        all(
            isinstance(value, int) and not isinstance(value, bool) and value > 0
            for value in counts.values()
        ),
        "contagem de exclusão inválida",
    )
    require(
        set(counts)
        <= _SNAPSHOT_EXCLUSION_REASONS
        | {
            "SEED_ACCOUNT_NOT_FOUND",
            "NOT_FETCHED_MAX_CANDIDATES",
            "NOT_FETCHED_MAX_MATCHES_PER_PLAYER",
        },
        "categoria de exclusão fora da allowlist",
    )
    expected_counts: Counter[str] = Counter(cast(dict[str, str], exclusions).values())
    expected_counts.update(
        f"NOT_FETCHED_{reason}" for reason in cast(dict[str, str], unknown_reasons).values()
    )
    for reason, amount in expected_counts.items():
        if counts.get(reason) != amount:
            raise DiscoveryError(
                f"contrato do snapshot inválido: contagem de exclusão divergente ({reason})"
            )
    require(
        counts.get("NOT_FETCHED_MAX_CANDIDATES", 0)
        == expected_counts.get("NOT_FETCHED_MAX_CANDIDATES", 0),
        "contagem de cap divergente",
    )
    require(
        counts.get("NOT_FETCHED_MAX_MATCHES_PER_PLAYER", 0)
        == expected_counts.get("NOT_FETCHED_MAX_MATCHES_PER_PLAYER", 0),
        "contagem por jogador divergente",
    )
    seed_not_found_count = counts.get("SEED_ACCOUNT_NOT_FOUND", 0)
    require(
        isinstance(seed_not_found_count, int)
        and not isinstance(seed_not_found_count, bool)
        and seed_not_found_count >= 0,
        "contagem de sementes inválida",
    )

    stop_reason = discovery.get("stopReason")
    if unknown:
        require(
            isinstance(stop_reason, str) and stop_reason in _SNAPSHOT_UNKNOWN_REASONS,
            "stopReason ausente para universo incompleto",
        )
        require(
            stop_reason
            == (
                "MAX_CANDIDATES"
                if "MAX_CANDIDATES" in unknown_reasons.values()
                else "MAX_MATCHES_PER_PLAYER"
            ),
            "stopReason não corresponde aos desconhecidos",
        )
    else:
        # A complete universe may still have stopped listing before the cap.
        require(
            stop_reason in (None, "MAX_CANDIDATES"),
            "stopReason incompatível com universo completo",
        )

    exploration = identifiers(partition.get("explorationMatchIds"), "explorationMatchIds")
    confirmation = identifiers(
        partition.get("confirmationPotentialMatchIds"), "confirmationPotentialMatchIds"
    )
    overlaps = identifiers(partition.get("overlapExcludedMatchIds"), "overlapExcludedMatchIds")
    require(set(exploration).isdisjoint(confirmation), "exploração/confirmação se sobrepõem")
    require(
        set(exploration).isdisjoint(overlaps) and set(confirmation).isdisjoint(overlaps),
        "partição/sobreposição se sobrepõem",
    )
    complete = not unknown
    usable = partition.get("usable")
    status = root.get("status")
    require(isinstance(usable, bool), "usable deve ser booleano")
    eligible = set(valid) - out_of_scope
    ordered_eligible = sorted(
        eligible,
        key=lambda match_id: (cast(str, digests[match_id]), match_id),
    )
    if not complete:
        require(
            status == "NO_SAMPLE" and usable is False,
            "universo incompleto deve ser NO_SAMPLE não utilizável",
        )
        require(
            not exploration and not confirmation and not overlaps,
            "universo incompleto contém partição",
        )
        require(
            all(
                match_id not in exclusions or exclusions[match_id] != "OUT_OF_SCOPE"
                for match_id in unknown
            ),
            "desconhecido tratado como OUT_OF_SCOPE",
        )
    else:
        expected_exploration = ordered_eligible[:20]
        require(exploration == expected_exploration, "exploração não segue hash/ordem congelados")
        if len(expected_exploration) < 20:
            require(
                status == "NO_SAMPLE" and usable is False and not confirmation and not overlaps,
                "amostra curta não é NO_SAMPLE",
            )
        else:
            tail = ordered_eligible[20:]
            require(set(overlaps) <= set(tail), "overlap fora da confirmação potencial")
            require(
                confirmation == [match_id for match_id in tail if match_id not in set(overlaps)],
                "confirmação altera ordem ou repõe exclusões",
            )
            require(
                status == "PARTITION_POTENTIAL_READY" and usable is True,
                "partição elegível deve estar READY",
            )
    require(
        partition.get("hashRule") == "sha256(CC-316-partition-v1|matchId), ascending",
        "regra de hash divergente",
    )
    if status == "PARTITION_POTENTIAL_READY":
        require(
            complete and usable is True and len(exploration) == 20,
            "READY sem os 20 IDs de exploração",
        )
    else:
        require(status == "NO_SAMPLE", "status de snapshot fora da allowlist")


def persist_snapshot_once(path: Path, artifact: dict[str, object]) -> str:
    """Validate the in-memory artifact before encoding or persisting it."""
    validate_snapshot_contract(artifact)
    payload = (json.dumps(artifact, indent=2, sort_keys=True) + "\n").encode("utf-8")
    return _atomic_write_once(path, payload)


async def run_cc316_discovery(
    *,
    transport: StructuralTransport,
    seeds: list[Seed],
    subject_hash_secret: str,
    artifact_path: Path,
    budget_journal_path: Path,
    attempt_number: int = 1,
    clock: Callable[[], float] = time.monotonic,
    process_started_monotonic_ns: int | None = None,
    boot_identifier: str | None = None,
    clock_ns: Callable[[], int] = time.monotonic_ns,
    temporal_baseline_path: Path | None = None,
) -> DiscoveryResult:
    """Discover through consented seeds and at most two participant hops.

    No timeline/raw import is performed. A durable one-shot journal reserves
    each outgoing HTTP attempt before it is sent.
    """
    if not subject_hash_secret:
        raise DiscoveryError("segredo HMAC ausente; nenhuma chamada permitida")
    if not seeds:
        raise DiscoveryError("lista de sementes vazia; nenhuma chamada permitida")
    if (
        isinstance(attempt_number, bool)
        or not isinstance(attempt_number, int)
        or attempt_number < 1
    ):
        raise DiscoveryError("número de tentativa inválido")
    if transport.requests_made:
        raise DiscoveryError("transporte já emitiu requisições; nova rodada exige instância limpa")
    started = clock()
    process_started = process_started_monotonic_ns
    if process_started is None:
        process_started = clock_ns()
    if (
        isinstance(process_started, bool)
        or not isinstance(process_started, int)
        or process_started < 0
    ):
        raise DiscoveryError("início monotônico do processo inválido")
    current_boot = boot_identifier or current_boot_id()
    if not any(part == ".private" for part in artifact_path.parent.parts):
        raise DiscoveryError("snapshot deve ficar dentro de .private")
    if budget_journal_path.parent.resolve() != artifact_path.parent.resolve():
        raise DiscoveryError("jornal e snapshot devem ficar no mesmo diretório privado")
    run_marker = artifact_path.parent / (
        ".cc316-discovery-consumed"
        if attempt_number == 1
        else f".cc316-discovery-consumed-{attempt_number:02d}"
    )
    prior_request_total, prior_elapsed_ns = validate_prior_budget_history(
        budget_journal_path,
        attempt_number,
        boot_identifier=current_boot,
        current_process_started_monotonic_ns=process_started,
        temporal_baseline_path=temporal_baseline_path,
    )
    require_candidate_budget_resolved()
    remaining_request_budget = MAX_REQUESTS - prior_request_total
    elapsed_before_coroutine_ns = clock_ns() - process_started
    if elapsed_before_coroutine_ns < 0:
        raise DiscoveryError("relógio monotônico retrocedeu dentro da mesma execução")
    remaining_time_ns = (
        _MAX_DURATION_NS
        - prior_elapsed_ns
        - elapsed_before_coroutine_ns
        - _ASYNCIO_EXECUTOR_SHUTDOWN_RESERVE_NS
    )
    remaining_time_budget = remaining_time_ns / 1_000_000_000
    if remaining_request_budget < 1:
        raise DiscoveryError("teto cumulativo de 1.000 requisições já consumido")
    if remaining_time_budget <= 0:
        raise DiscoveryError(
            "orçamento de processo insuficiente/incomparável; continuidade exige "
            "decisão/emenda explícita"
        )
    if artifact_path.exists() or run_marker.exists():
        raise DiscoveryError("snapshot de partição já existe; não será sobrescrito")

    lock_path = artifact_path.parent / ".cc316-discovery.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    if artifact_path.parent.stat().st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise DiscoveryError("diretório privado permite escrita por grupo ou outros usuários")
    lock_handle = lock_path.open("a+")
    try:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        lock_handle.close()
        raise DiscoveryError("há outra rodada CC-316 em andamento") from exc
    if artifact_path.exists():
        lock_handle.close()
        raise DiscoveryError("snapshot de partição já existe; não será sobrescrito")
    if run_marker.exists():
        lock_handle.close()
        raise DiscoveryError("rodada CC-316 já iniciada; nova tentativa exige revisão manual")
    if artifact_path.parent.stat().st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        lock_handle.close()
        raise DiscoveryError("diretório privado permite escrita por grupo ou outros usuários")
    marker_fd = os.open(run_marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        os.write(marker_fd, datetime.now(UTC).isoformat().encode("ascii"))
        os.fsync(marker_fd)
    finally:
        os.close(marker_fd)

    request_start = transport.requests_made
    records: dict[str, StructuralMetadata] = {}
    source_depth: dict[str, int] = {}
    source_by_match_id: dict[str, str] = {}
    structural_exclusions: Counter[str] = Counter()
    queried_players: set[str] = set()
    known_match_ids: set[str] = set()
    failed_match_ids: dict[str, str] = {}
    queried_match_ids: set[str] = set()
    unattempted_reason_by_match_id: dict[str, str] = {}
    frontier: list[tuple[str, int]] = []
    candidate_cap_reached = False

    try:
        with BudgetJournal(budget_journal_path, limit=remaining_request_budget) as journal:
            transport.set_request_limit(remaining_request_budget)
            transport.set_total_time_limit(remaining_time_budget)
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

                for seed in seeds:
                    ensure_time_budget()
                    try:
                        puuid = await transport.resolve_account_puuid(
                            seed.account.game_name, seed.account.tag_line
                        )
                    except SourceNotFoundError:
                        structural_exclusions["SEED_ACCOUNT_NOT_FOUND"] += 1
                        continue
                    frontier.append((puuid, 0))

                for depth in range(DISCOVERY_DEPTH + 1):
                    next_frontier: dict[str, int] = {}
                    fresh_match_ids: list[str] = []
                    for puuid, player_depth in frontier:
                        if player_depth != depth or not puuid:
                            continue
                        player_digest = hashlib.sha256(
                            b"CC-316-player-visit-v1|" + puuid.encode("utf-8")
                        ).hexdigest()
                        if player_digest in queried_players:
                            continue
                        # Stop before a listing could exceed the cap; every ID a
                        # listing returns must fit, or the universe is incomplete.
                        if len(known_match_ids) + MAX_MATCHES_PER_PLAYER > MAX_CANDIDATES:
                            candidate_cap_reached = True
                            break
                        queried_players.add(player_digest)
                        ensure_time_budget()
                        if remaining_request_budget - journal.requests_reserved < 2:
                            raise DiscoveryError(
                                "orçamento restante insuficiente para consultar jogador"
                            )
                        ids = await transport.list_match_ids_by_puuid(
                            puuid, queue=TARGET_QUEUE, start=0, count=MAX_MATCHES_PER_PLAYER
                        )
                        fresh_for_player = 0
                        # Preserve the full returned list for an auditable universe.
                        # The per-player limit applies to metadata fetches, not to
                        # forgetting IDs returned by the endpoint.
                        distinct_for_player = list(dict.fromkeys(ids))
                        for match_id in distinct_for_player:
                            if match_id not in queried_match_ids:
                                queried_match_ids.add(match_id)
                                source_depth[match_id] = depth
                                source_by_match_id[match_id] = (
                                    "CONSENTED_SEED_MATCH_LIST"
                                    if depth == 0
                                    else "PARTICIPANT_MATCH_LIST"
                                )
                            elif depth < source_depth[match_id]:
                                source_depth[match_id] = depth
                                source_by_match_id[match_id] = (
                                    "CONSENTED_SEED_MATCH_LIST"
                                    if depth == 0
                                    else "PARTICIPANT_MATCH_LIST"
                                )
                        for match_id in distinct_for_player:
                            if match_id in known_match_ids:
                                continue
                            if len(known_match_ids) >= MAX_CANDIDATES:
                                candidate_cap_reached = True
                                unattempted_reason_by_match_id.setdefault(
                                    match_id, "MAX_CANDIDATES"
                                )
                                continue
                            if fresh_for_player >= MAX_MATCHES_PER_PLAYER:
                                unattempted_reason_by_match_id.setdefault(
                                    match_id, "MAX_MATCHES_PER_PLAYER"
                                )
                                continue
                            known_match_ids.add(match_id)
                            unattempted_reason_by_match_id.pop(match_id, None)
                            fresh_match_ids.append(match_id)
                            fresh_for_player += 1
                        if candidate_cap_reached:
                            break

                    for match_id in fresh_match_ids:
                        ensure_time_budget()
                        discovered_puuids: list[str] = []
                        try:
                            body = await transport.fetch_match_for_structural_projection(match_id)
                            record = parse_structural_metadata(
                                body,
                                requested_match_id=match_id,
                                subject_hash_secret=subject_hash_secret,
                                participant_callback=discovered_puuids.append
                                if depth < DISCOVERY_DEPTH
                                else None,
                            )
                            del body
                            records[match_id] = record
                            if depth < DISCOVERY_DEPTH:
                                for puuid in discovered_puuids:
                                    digest = hashlib.sha256(
                                        b"CC-316-player-visit-v1|" + puuid.encode("utf-8")
                                    ).hexdigest()
                                    if digest not in queried_players:
                                        next_frontier.setdefault(puuid, depth + 1)
                        except SourceNotFoundError:
                            structural_exclusions["MATCH_NOT_FOUND"] += 1
                            failed_match_ids[match_id] = "MATCH_NOT_FOUND"
                        except StructuralMetadataError as exc:
                            reason = type(exc).__name__
                            structural_exclusions[reason] += 1
                            failed_match_ids[match_id] = reason
                        finally:
                            discovered_puuids.clear()

                    frontier = [(puuid, next_depth) for puuid, next_depth in next_frontier.items()]
                    if candidate_cap_reached:
                        break

                unknown_due_to_cap = queried_match_ids - known_match_ids
                if unknown_due_to_cap != set(unattempted_reason_by_match_id):
                    raise DiscoveryError("snapshot incompleto: motivo de metadado ausente")
                ensure_time_budget()
                partition = build_partition(list(records.values()))
                # IDs whose Match-V5 was not fetched can sort ahead of known rows.
                # Do not claim the first 20 eligible rows until the universe is complete.
                universe_complete = not unknown_due_to_cap
                exclusion_counts = Counter(structural_exclusions)
                exclusion_counts.update("OUT_OF_SCOPE" for _ in partition.structural_exclusions)
                for reason in unattempted_reason_by_match_id.values():
                    exclusion_counts[f"NOT_FETCHED_{reason}"] += 1
                stop_reason: str | None = None
                if unknown_due_to_cap:
                    stop_reason = (
                        "MAX_CANDIDATES"
                        if "MAX_CANDIDATES" in unattempted_reason_by_match_id.values()
                        else "MAX_MATCHES_PER_PLAYER"
                    )
                elif candidate_cap_reached:
                    stop_reason = "MAX_CANDIDATES"
                status = (
                    "PARTITION_POTENTIAL_READY"
                    if partition.status == "READY" and universe_complete
                    else "NO_SAMPLE"
                )
                result_partition = (
                    partition
                    if universe_complete
                    else PartitionResult(
                        exploration_ids=(),
                        confirmation_ids=(),
                        overlap_exclusions=(),
                        structural_exclusions=partition.structural_exclusions,
                        status="NO_SAMPLE",
                    )
                )
                artifact = {
                    "schemaVersion": 1,
                    "protocol": "CC-316-A",
                    "createdAt": datetime.now(UTC).isoformat(),
                    "status": status,
                    "target": {
                        "patch": TARGET_PATCH,
                        "queueId": TARGET_QUEUE,
                        "mapId": TARGET_MAP,
                        "gameComplete": True,
                    },
                    "discovery": {
                        "source": "CONSENTED_SEEDS_PLUS_PARTICIPANTS",
                        "maximumParticipantHops": DISCOVERY_DEPTH,
                        "maximumMatchesPerPlayer": MAX_MATCHES_PER_PLAYER,
                        "uniqueCandidateCount": len(queried_match_ids),
                        "metadataRequestAttemptCount": len(known_match_ids),
                        "structuralMetadataValidCount": len(records),
                        "stopReason": stop_reason,
                        "candidateProvenanceByMatchId": {
                            match_id: {
                                "depth": source_depth[match_id],
                                "source": source_by_match_id[match_id],
                            }
                            for match_id in sorted(queried_match_ids)
                        },
                        "metadataRequestAttemptedMatchIds": sorted(known_match_ids),
                        "structuralMetadataValidMatchIds": sorted(records),
                        "metadataNotAttemptedMatchIds": sorted(unknown_due_to_cap),
                        "metadataUnknownReasonByMatchId": {
                            match_id: unattempted_reason_by_match_id.get(
                                match_id, "UNKNOWN_CAP_REASON"
                            )
                            for match_id in sorted(unknown_due_to_cap)
                        },
                    },
                    "partition": {
                        "hashRule": "sha256(CC-316-partition-v1|matchId), ascending",
                        "partitionDigestByMatchId": {
                            match_id: hashlib.sha256(
                                f"CC-316-partition-v1|{match_id}".encode()
                            ).hexdigest()
                            for match_id in sorted(queried_match_ids)
                        },
                        "candidateMatchIds": sorted(queried_match_ids),
                        "explorationMatchIds": list(partition.exploration_ids)
                        if universe_complete
                        else [],
                        "confirmationPotentialMatchIds": list(partition.confirmation_ids)
                        if universe_complete
                        else [],
                        "overlapExcludedMatchIds": list(partition.overlap_exclusions)
                        if universe_complete
                        else [],
                        "usable": universe_complete and partition.status == "READY",
                        "structuralExcludedMatchIds": {
                            **{
                                match_id: "OUT_OF_SCOPE"
                                for match_id in partition.structural_exclusions
                            },
                            **failed_match_ids,
                        },
                        "exclusionCounts": dict(sorted(exclusion_counts.items())),
                    },
                    "privacy": {
                        "participantPuuidsPersisted": False,
                        "participantHmacsPersisted": False,
                        "matchBodiesPersisted": False,
                        "timelineFetched": False,
                    },
                    "requests": transport.requests_made - request_start,
                    "elapsedSeconds": round(clock() - started, 3),
                }
                digest = persist_snapshot_once(artifact_path, artifact)
                journal.finish(status="COMPLETED")
                summary = DiscoverySummary(
                    status=status,
                    seed_count=len(seeds),
                    players_queried=len(queried_players),
                    candidates_seen=len(queried_match_ids),
                    metadata_fetched=len(records),
                    structural_exclusions=dict(sorted(exclusion_counts.items())),
                    exploration_count=len(result_partition.exploration_ids),
                    confirmation_potential_count=len(result_partition.confirmation_ids),
                    overlap_exclusions=len(result_partition.overlap_exclusions),
                    requests=transport.requests_made - request_start,
                    elapsed_seconds=round(clock() - started, 3),
                    artifact_path=str(artifact_path),
                    artifact_sha256=digest,
                )
                return DiscoveryResult(summary=summary, partition=result_partition)
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
