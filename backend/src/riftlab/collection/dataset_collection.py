"""CC-323/CC-703: snowball collection of patch 16.19 match+timeline pairs for the datasets.

Expansion starts at the consented seed accounts and follows every participant of
the matches it finds (opponents and teammates) with no fixed hop limit. Players
seen in the campaign scope (BOTTOM with one of its champions) are listed before
the others. Only completed 16.19 / queue 420 / map 11 matches are stored, as
write-once pairs in a private raw store. PUUIDs stay in memory and inside the
private raw bodies; journals and reports carry none.

A `Campaign` fixes everything one authorization covers: identity, output directory,
window, caps, scope and how a player's listing is reduced. CC-323 keeps the most
recent matches of each player; CC-703 (CC-702 scope, ten marksmen) lists up to 100
matches in the window and keeps a salted-hash sample, so a player's matches spread
over the patch at the same request cost.
"""

from __future__ import annotations

import fcntl
import hashlib
import heapq
import json
import math
import os
import re
import stat
import time
import unicodedata
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, cast

from riftlab.collection.cc316_adoption import (
    canonical_json,
    private_root_for,
    read_private_with_sidecar,
)
from riftlab.collection.cc316_discovery import (
    _DIAGNOSTIC_CAUSE_CATEGORIES,
    _DIAGNOSTIC_EXCEPTION_CLASSES,
    _DIAGNOSTIC_REQUEST_CLASSES,
    _DIAGNOSTIC_RESULTS,
    BudgetJournal,
    DiscoveryError,
    _atomic_write_once,
)
from riftlab.collection.seeds import Seed
from riftlab.ingestion.infrastructure.raw_store import DurableRawMatchStore
from riftlab.ingestion.infrastructure.riot_transport import (
    RawRiotResponse,
    _diagnostic_result_for_status,
)
from riftlab.shared.errors import (
    SourceAuthError,
    SourceError,
    SourceNotFoundError,
    SourceRateLimitedError,
    SourceSchemaError,
)

CAMPAIGN_ID = "CC-323"
AUTHORIZATION_SCOPE = "DATASET_COLLECTION"
TARGET_PATCH = "16.19"
TARGET_QUEUE = 420
TARGET_MAP = 11
# Data Dragon publication times (CDN Last-Modified) of 16.19.1 and 16.20.1.
WINDOW_START = 1_790_197_750  # 2026-09-23T21:09:10Z
WINDOW_END = 1_791_321_231  # 2026-10-06T21:13:51Z
LISTING_COUNT = 5
MAX_PAIRS = 1_000
MAX_REQUESTS = 4_000
MAX_SECONDS = 6_000
SCOPE_CHAMPIONS = frozenset({51, 81, 145})
SCOPE_POSITION = "BOTTOM"
DESIGNED_STOPS = frozenset(
    {"PAIR_CAP_REACHED", "QUEUE_EXHAUSTED", "REQUEST_CAP_REACHED", "TIME_CAP_REACHED"}
)
_SHUTDOWN_RESERVE_NS = 300_000_000_000
_MATCH_ID = re.compile(r"^BR1_[0-9]{1,20}$")
_PATCH = re.compile(r"^[0-9]{1,3}\.[0-9]{1,3}$")
_CAMPAIGN_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,47}$")
_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
_TERMINAL_JOURNAL_STATUSES = frozenset(
    {"COMPLETED", "INTERRUPTED", "STOPPED_AUTH", "STOPPED_RATE_OR_BUDGET"}
)
_LIFECYCLE_FIELDS = (
    "processStartedMonotonicNs",
    "coroutineStartedMonotonicNs",
    "coroutineFinishedMonotonicNs",
    "processFinishedMonotonicNs",
)
_LOCAL_STOPS = {
    "LOCAL_REQUEST_BUDGET": "REQUEST_CAP_REACHED",
    "LOCAL_TIME_BUDGET": "TIME_CAP_REACHED",
}
_PROGRESS_COUNTS = frozenset(
    {
        "LISTED_IDS_NOT_SAMPLED",
        "MATCHES_FETCHED",
        "MATCHES_LISTED",
        "PAIRS_STORED",
        "PAIR_ALREADY_PRESENT",
        "PAIR_KNOWN_ELSEWHERE",
        "PLAYERS_LISTED",
        "SCOPE_SLOTS_IN_NEW_PAIRS",
    }
)
_SEED_PRIORITY, _SCOPE_PRIORITY, _OTHER_PRIORITY = 0, 1, 2


@dataclass(frozen=True, slots=True)
class Campaign:
    campaign_id: str
    output_dir_name: str
    window_start: int
    window_end: int
    max_pairs: int
    max_requests: int
    max_seconds: int
    scope_champions: frozenset[int]
    listing_count: int
    # None keeps the `listing_count` most recent matches (CC-323). An int keeps that
    # many of the listed IDs, ordered by sha256(salt|puuid|matchId): same request cost,
    # matches spread over the player's whole window, reproducible without RNG state.
    sample_per_player: int | None = None
    sample_salt: str = ""
    known_raw_subdirectories: tuple[str, ...] = ("cc316/raw-exploration", "cc316/raw-confirmation")
    patch: str = TARGET_PATCH
    queues: tuple[int, ...] = (TARGET_QUEUE,)
    map_id: int = TARGET_MAP
    schema_version: int = 1
    scope_position: str = SCOPE_POSITION
    campaign_name: str | None = None

    @property
    def kept_per_player(self) -> int:
        return self.listing_count if self.sample_per_player is None else self.sample_per_player

    @property
    def is_generic(self) -> bool:
        return self.schema_version == 2

    def keep(self, puuid: str, match_ids: Sequence[str]) -> list[str]:
        if self.sample_per_player is None:
            return list(match_ids)
        ranked = sorted(
            dict.fromkeys(match_ids),
            key=lambda match_id: hashlib.sha256(
                f"{self.sample_salt}|{puuid}|{match_id}".encode()
            ).hexdigest(),
        )
        return ranked[: self.sample_per_player]


CC323 = Campaign(
    campaign_id=CAMPAIGN_ID,
    output_dir_name="cc323",
    window_start=WINDOW_START,
    window_end=WINDOW_END,
    max_pairs=MAX_PAIRS,
    max_requests=MAX_REQUESTS,
    max_seconds=MAX_SECONDS,
    scope_champions=SCOPE_CHAMPIONS,
    listing_count=LISTING_COUNT,
)
# CC-703 (user decision 14/10/2026): up to 10,000 new pairs, 24,000 requests and 10 h,
# prioritising the ten marksmen of configs/experiments/scope-16.19-marksmen10.yaml.
CC703 = Campaign(
    campaign_id="CC-703",
    output_dir_name="cc703",
    window_start=WINDOW_START,
    window_end=WINDOW_END,
    max_pairs=10_000,
    max_requests=24_000,
    max_seconds=36_000,
    scope_champions=frozenset({18, 21, 22, 29, 51, 81, 145, 202, 222, 804}),
    listing_count=100,
    sample_per_player=5,
    sample_salt="CC-703-listing-v1",
    known_raw_subdirectories=("cc316/raw-exploration", "cc316/raw-confirmation", "cc323/raw"),
)
CAMPAIGNS = {campaign.campaign_id: campaign for campaign in (CC323, CC703)}


def build_patch_campaign(
    *,
    patch: str,
    campaign_name: str,
    window_start: int,
    window_end: int,
    max_pairs: int = 1_200,
    max_requests: int = 3_000,
    max_seconds: int = 7_200,
    queues: tuple[int, ...] = (420, 440),
    map_id: int = TARGET_MAP,
    scope_champions: frozenset[int] = SCOPE_CHAMPIONS,
    scope_position: str = SCOPE_POSITION,
    known_raw_subdirectories: tuple[str, ...] = (
        "cc316/raw-exploration",
        "cc316/raw-confirmation",
        "cc323/raw",
        "cc703/raw",
        "preview-real/raw",
    ),
) -> Campaign:
    """Create a validated v2 profile; the legacy named campaigns remain v1."""
    name = unicodedata.normalize("NFKC", campaign_name)
    if name != campaign_name or not _CAMPAIGN_NAME.fullmatch(name):
        raise DiscoveryError("nome da campanha deve ser ASCII seguro (a-z, 0-9, _ ou -)")
    if not isinstance(patch, str) or not _PATCH.fullmatch(patch):
        raise DiscoveryError("patch da campanha deve ser major.minor numérico")
    if (
        isinstance(window_start, bool)
        or isinstance(window_end, bool)
        or not isinstance(window_start, int)
        or not isinstance(window_end, int)
        or window_start < 0
        or window_start >= window_end
    ):
        raise DiscoveryError("janela da campanha inválida")
    if (
        not queues
        or len(set(queues)) != len(queues)
        or any(isinstance(q, bool) or not isinstance(q, int) or q not in {420, 440} for q in queues)
    ):
        raise DiscoveryError("a campanha aceita somente filas distintas 420 e/ou 440")
    if map_id != TARGET_MAP:
        raise DiscoveryError("a campanha patch aceita somente o mapa 11")
    if (
        not isinstance(max_pairs, int)
        or isinstance(max_pairs, bool)
        or not 1 <= max_pairs <= 1_200
        or not isinstance(max_requests, int)
        or isinstance(max_requests, bool)
        or not 1 <= max_requests <= 3_000
        or not isinstance(max_seconds, int)
        or isinstance(max_seconds, bool)
        or not 1 <= max_seconds <= 7_200
    ):
        raise DiscoveryError("tetos da campanha genérica excedem os limites técnicos")
    if not scope_champions or any(
        isinstance(champion, bool) or not isinstance(champion, int) or champion <= 0
        for champion in scope_champions
    ):
        raise DiscoveryError("champions da prioridade devem ser IDs inteiros positivos")
    if scope_position != SCOPE_POSITION:
        raise DiscoveryError("prioridade de campanha usa a posição BOTTOM congelada")
    campaign_id = f"PATCH-CAMPAIGN-{patch}-{name}"
    return Campaign(
        campaign_id=campaign_id,
        output_dir_name=f"patch-{patch.replace('.', '-')}-{name}",
        window_start=window_start,
        window_end=window_end,
        max_pairs=max_pairs,
        max_requests=max_requests,
        max_seconds=max_seconds,
        scope_champions=scope_champions,
        listing_count=100,
        sample_per_player=5,
        sample_salt=f"{campaign_id}-listing-v1",
        known_raw_subdirectories=known_raw_subdirectories,
        patch=patch,
        queues=queues,
        map_id=map_id,
        schema_version=2,
        scope_position=scope_position,
        campaign_name=name,
    )


class CollectionTransport(Protocol):
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

    async def fetch_match(self, match_id: str) -> RawRiotResponse: ...

    async def fetch_timeline(self, match_id: str) -> RawRiotResponse: ...


@dataclass(frozen=True, slots=True)
class CollectionAuthorization:
    max_requests: int
    seeds_sha256: str
    schema_version: int = 1
    manifest_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class CollectionSummary:
    status: str
    pairs_new: int
    pairs_stored_total: int
    pairs_known_elsewhere: int
    players_listed: int
    matches_listed: int
    matches_fetched: int
    scope_slots_in_new_pairs: int
    discarded: dict[str, int]
    requests: int
    elapsed_seconds: float
    report_path: str
    report_sha256: str


class _CapReached(Exception):
    def __init__(self, status: str) -> None:
        super().__init__(status)
        self.status = status


def attempt_paths(output_dir: Path, attempt_number: int) -> dict[str, Path]:
    suffix = f"{attempt_number:02d}"
    return {
        "budget": output_dir / f"request-budget-{suffix}.json",
        "lifecycle": output_dir / f"process-lifecycle-{suffix}.json",
        "marker": output_dir / f".collection-started-{suffix}",
        "report": output_dir / f"collection-attempt-{suffix}.json",
        "raw": output_dir / "raw",
    }


def _non_negative_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def prior_campaign_usage(output_dir: Path, attempt_number: int) -> tuple[int, int]:
    """Sum the requests and process time of every earlier attempt of the campaign.

    Each lifecycle measures one process on one monotonic clock, so the
    durations add up across reboots. A missing, unfinished or incoherent
    attempt blocks the campaign until someone reviews it.
    """
    requests = elapsed_ns = 0
    for previous in range(1, attempt_number):
        paths = attempt_paths(output_dir, previous)
        if not paths["marker"].exists():
            raise DiscoveryError(f"tentativa {previous:02d} da campanha sem marcador; revisão")
        try:
            budget = json.loads(paths["budget"].read_text(encoding="utf-8"))
            lifecycle = json.loads(paths["lifecycle"].read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeError) as exc:
            raise DiscoveryError(
                f"tentativa {previous:02d} da campanha ilegível ou incompleta; revisão"
            ) from exc
        if not isinstance(budget, dict) or not isinstance(lifecycle, dict):
            raise DiscoveryError(f"tentativa {previous:02d} da campanha incoerente; revisão")
        marks = [lifecycle.get(field) for field in _LIFECYCLE_FIELDS]
        elapsed = lifecycle.get("processElapsedNs")
        coherent = (
            budget.get("schemaVersion") == 3
            and budget.get("status") in _TERMINAL_JOURNAL_STATUSES
            and _non_negative_int(budget.get("requestsReserved"))
            and lifecycle.get("attemptNumber") == previous
            and lifecycle.get("lifecycleStatus") in {"COMPLETE", "SHUTDOWN_TIMEOUT"}
            and all(_non_negative_int(mark) for mark in marks)
            and _non_negative_int(elapsed)
        )
        if coherent:
            started, coroutine_started, coroutine_finished, finished = cast(list[int], marks)
            coherent = (
                started <= coroutine_started <= coroutine_finished <= finished
                and elapsed == finished - started
            )
        if not coherent:
            raise DiscoveryError(f"tentativa {previous:02d} da campanha incoerente; revisão")
        requests += cast(int, budget["requestsReserved"])
        elapsed_ns += cast(int, elapsed)
    return requests, elapsed_ns


def prior_patch_campaign_usage(
    output_dir: Path, attempt_number: int, manifest_sha256: str
) -> tuple[int, int]:
    """Validate each completed v2 history against its request diagnostics and manifest."""
    if not _SHA256_HEX.fullmatch(manifest_sha256):
        raise DiscoveryError("hash do manifesto da campanha inválido")
    private_root = private_root_for(output_dir)
    if any(part.is_symlink() for part in (output_dir, *output_dir.parents)):
        raise DiscoveryError("caminho de campanha v2 contém symlink")
    try:
        output_dir.resolve().relative_to(private_root.resolve())
    except ValueError as exc:
        raise DiscoveryError("saída de campanha v2 fora de .private") from exc
    manifest_path = output_dir / PATCH_CAMPAIGN_MANIFEST
    _manifest, actual_manifest_sha = read_private_with_sidecar(
        manifest_path, "manifesto da campanha"
    )
    if actual_manifest_sha != manifest_sha256:
        raise DiscoveryError("manifesto da campanha diverge do histórico")
    requests_total = elapsed_total = 0
    for previous in range(1, attempt_number):
        paths = attempt_paths(output_dir, previous)
        if any(
            part.is_symlink()
            for key in ("budget", "lifecycle", "report", "marker")
            for part in (paths[key], *paths[key].parents)
        ):
            raise DiscoveryError(f"histórico v2 da tentativa {previous:02d} contém symlink")
        if not paths["marker"].is_file():
            raise DiscoveryError(f"histórico v2 da tentativa {previous:02d} sem marcador")
        binding = {
            "campaign": json.loads(_manifest).get("campaign"),
            "attemptNumber": previous,
            "manifestSha256": manifest_sha256,
        }
        try:
            budget = json.loads(paths["budget"].read_text(encoding="utf-8"))
            lifecycle = json.loads(paths["lifecycle"].read_text(encoding="utf-8"))
            report = json.loads(paths["report"].read_text(encoding="utf-8"))
            diagnostics = budget.get("diagnostics") if isinstance(budget, dict) else None
            reserved = budget.get("requestsReserved") if isinstance(budget, dict) else None
            if not isinstance(diagnostics, list) or not _non_negative_int(reserved):
                raise ValueError("diagnostics")
            http_rows = [
                row
                for row in diagnostics
                if isinstance(row, dict) and row.get("requestReserved") is True
            ]
            local_rows = [
                row
                for row in diagnostics
                if isinstance(row, dict) and row.get("requestReserved") is False
            ]
            journal_status = budget.get("status")
            http_diagnostics_coherent = True
            expected_diagnostic_keys = {
                "sequence",
                "ordinal",
                "requestClass",
                "result",
                "statusCode",
                "durationMs",
                "exceptionClass",
                "causeCategory",
                "requestReserved",
            }
            for row in diagnostics:
                if not isinstance(row, dict) or set(row) != expected_diagnostic_keys:
                    http_diagnostics_coherent = False
                    break
                result = row.get("result")
                status_code = row.get("statusCode")
                duration = row.get("durationMs")
                exception_class = row.get("exceptionClass")
                cause = row.get("causeCategory")
                if type(row.get("requestReserved")) is not bool:
                    http_diagnostics_coherent = False
                    break
                if row["requestReserved"]:
                    duration_valid = (
                        isinstance(duration, (int, float))
                        and not isinstance(duration, bool)
                        and math.isfinite(duration)
                        and duration >= 0
                    )
                    if result == "PENDING":
                        coherent = (
                            journal_status == "INTERRUPTED"
                            and status_code is None
                            and duration is None
                            and exception_class is None
                            and cause is None
                        )
                    elif result == "HTTP_ERROR":
                        coherent = (
                            status_code is None
                            and duration_valid
                            and exception_class in _DIAGNOSTIC_EXCEPTION_CLASSES
                            and cause in _DIAGNOSTIC_CAUSE_CATEGORIES
                        )
                    else:
                        coherent = (
                            isinstance(status_code, int)
                            and not isinstance(status_code, bool)
                            and 100 <= status_code <= 599
                            and result == _diagnostic_result_for_status(status_code)
                            and duration_valid
                            and exception_class is None
                            and cause is None
                        )
                else:
                    coherent = (
                        result in {"LOCAL_REQUEST_BUDGET", "LOCAL_TIME_BUDGET"}
                        and row.get("ordinal") is None
                        and status_code is None
                        and duration is None
                        and exception_class is None
                        and cause is None
                    )
                if not coherent:
                    http_diagnostics_coherent = False
                    break
            ordinals = [row.get("ordinal") for row in http_rows]
            sequences = [row.get("sequence") for row in diagnostics if isinstance(row, dict)]
            if (
                len(http_rows) != reserved
                or not all(_non_negative_int(value) for value in ordinals)
                or sorted(cast(list[int], ordinals)) != list(range(1, reserved + 1))
                or len(sequences) != len(diagnostics)
                or not all(_non_negative_int(value) for value in sequences)
                or sorted(cast(list[int], sequences)) != list(range(1, len(diagnostics) + 1))
                or any(row.get("result") not in _DIAGNOSTIC_RESULTS for row in http_rows)
                or any(
                    row.get("result") not in {"LOCAL_REQUEST_BUDGET", "LOCAL_TIME_BUDGET"}
                    for row in local_rows
                )
                or any(
                    row.get("requestClass") not in _DIAGNOSTIC_REQUEST_CLASSES
                    for row in diagnostics
                    if isinstance(row, dict)
                )
                or not http_diagnostics_coherent
                or report.get("requests") != reserved
                or report.get("campaignManifestSha256") != manifest_sha256
                or report.get("status")
                not in DESIGNED_STOPS
                | {
                    "STOPPED_AUTH",
                    "STOPPED_RATE_LIMITED",
                    "STOPPED_SOURCE_ERROR",
                    "INTERRUPTED",
                }
            ):
                raise ValueError("coerência")
            marks = [lifecycle.get(field) for field in _LIFECYCLE_FIELDS]
            elapsed = lifecycle.get("processElapsedNs")
            if (
                budget.get("schemaVersion") != 4
                or budget.get("campaignBinding") != binding
                or budget.get("status") not in _TERMINAL_JOURNAL_STATUSES
                or lifecycle.get("schemaVersion") != 1
                or lifecycle.get("campaignBinding") != binding
                or lifecycle.get("attemptNumber") != previous
                or lifecycle.get("lifecycleStatus") not in {"COMPLETE", "SHUTDOWN_TIMEOUT"}
                or lifecycle.get("runOutcome") not in {"COMPLETED", "INTERRUPTED"}
                or (lifecycle.get("runOutcome") == "INTERRUPTED")
                != (budget.get("status") == "INTERRUPTED")
                or not all(_non_negative_int(mark) for mark in marks)
                or not _non_negative_int(elapsed)
            ):
                raise ValueError("binding/lifecycle")
            started, coroutine_started, coroutine_finished, finished = cast(list[int], marks)
            if not (
                started <= coroutine_started <= coroutine_finished <= finished
                and elapsed == finished - started
            ):
                raise ValueError("tempo")
            if budget.get("status") == "COMPLETED" and any(
                row.get("result") == "PENDING" for row in http_rows
            ):
                raise ValueError("request pendente em journal completo")
            requests_total += reserved
            elapsed_total += cast(int, elapsed)
        except (OSError, json.JSONDecodeError, UnicodeError, ValueError, TypeError) as exc:
            raise DiscoveryError(
                f"histórico v2 da tentativa {previous:02d} incoerente; revisão"
            ) from exc
    return requests_total, elapsed_total


PATCH_CAMPAIGN_MANIFEST = "campaign-manifest-v2.json"


def effective_campaign_source_sha256() -> str:
    """Fingerprint the source files that can affect the v2 campaign before network use."""
    source_root = Path(__file__).resolve().parents[3]
    relative_files = (
        "src/riftlab/cli/collect.py",
        "src/riftlab/collection/dataset_collection.py",
        "src/riftlab/collection/cc316_discovery.py",
        "src/riftlab/collection/cc316_adoption.py",
        "src/riftlab/collection/seeds.py",
        "src/riftlab/ingestion/infrastructure/riot_transport.py",
        "src/riftlab/ingestion/infrastructure/riot_rate_limiter.py",
        "src/riftlab/ingestion/infrastructure/raw_store.py",
    )
    digest = hashlib.sha256()
    for relative in relative_files:
        path = source_root / relative
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def validate_private_campaign_path(private_root: Path, path: Path, label: str) -> None:
    """Reject a campaign path that escapes the private root or traverses a symlink."""
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise DiscoveryError(f"{label} contém symlink")
    try:
        path.resolve().relative_to(private_root.resolve())
    except ValueError as exc:
        raise DiscoveryError(f"{label} fora de .private") from exc


def patch_campaign_manifest(
    campaign: Campaign,
    *,
    private_root: Path,
    output_dir: Path,
    seeds_sha256: str,
    scope_sha256: str,
    tool_sha256: str,
) -> dict[str, object]:
    """Canonical immutable v2 campaign identity, including inputs and storage roots."""
    if not campaign.is_generic or campaign.campaign_name is None:
        raise DiscoveryError("manifesto v2 exige campanha genérica")
    resolved_private = private_root.resolve()
    resolved_output = output_dir.resolve()
    try:
        relative_output = resolved_output.relative_to(resolved_private)
    except ValueError as exc:
        raise DiscoveryError("raiz da campanha deve ficar dentro de .private") from exc
    if any(component.is_symlink() for component in (private_root, *output_dir.parents, output_dir)):
        raise DiscoveryError("caminho da campanha contém symlink")
    roots = list(campaign.known_raw_subdirectories)
    if any(Path(root).is_absolute() or ".." in Path(root).parts for root in roots):
        raise DiscoveryError("raiz de raw conhecida fora do contrato privado")
    for root in roots:
        candidate = private_root / root
        validate_private_campaign_path(private_root, candidate, "raiz raw conhecida")
    for digest in (seeds_sha256, scope_sha256, tool_sha256):
        if not isinstance(digest, str) or not _SHA256_HEX.fullmatch(digest):
            raise DiscoveryError("hash de entrada do manifesto inválido")
    return {
        "schemaVersion": 2,
        "campaign": campaign.campaign_id,
        "campaignName": campaign.campaign_name,
        "target": {
            "patch": campaign.patch,
            "queueIds": list(campaign.queues),
            "mapId": campaign.map_id,
            "gameComplete": True,
        },
        "window": {"startTime": campaign.window_start, "endTime": campaign.window_end},
        "caps": {
            "maxPairs": campaign.max_pairs,
            "maxRequests": campaign.max_requests,
            "maxSeconds": campaign.max_seconds,
        },
        "expansion": {
            "listingCount": campaign.listing_count,
            "samplePerPlayerPerQueue": campaign.sample_per_player,
            "sampleSalt": campaign.sample_salt,
            "scopeChampionIds": sorted(campaign.scope_champions),
            "scopePosition": campaign.scope_position,
        },
        "roots": {
            "output": relative_output.as_posix(),
            "knownRaw": roots,
        },
        "inputHashes": {
            "seedsSha256": seeds_sha256,
            "scopeSha256": scope_sha256,
            "toolSha256": tool_sha256,
        },
    }


def persist_patch_campaign_manifest_once(output_dir: Path, manifest: dict[str, object]) -> str:
    """Write a v2 manifest exactly once, or verify byte-identical continuation."""
    path = output_dir / PATCH_CAMPAIGN_MANIFEST
    encoded = canonical_json(manifest)
    digest = hashlib.sha256(encoded).hexdigest()
    if path.exists() or path.with_suffix(path.suffix + ".sha256").exists():
        existing, existing_digest = read_private_with_sidecar(path, "manifesto da campanha")
        if existing != encoded or existing_digest != digest:
            raise DiscoveryError("manifesto da campanha diverge; não será sobrescrito")
        return digest
    _atomic_write_once(path, encoded)
    return digest


def load_collection_authorization(
    path: Path,
    *,
    attempt_number: int,
    campaign: Campaign = CC323,
    manifest_sha256: str | None = None,
) -> CollectionAuthorization:
    """Verify the user's authorization of one campaign attempt (JSON with SHA-256 sidecar)."""
    label = f"autorização {campaign.campaign_id} inválida"

    def require(condition: bool, message: str) -> None:
        if not condition:
            raise DiscoveryError(f"{label}: {message}")

    private_root = private_root_for(path)
    raw, _digest = read_private_with_sidecar(path, f"autorização {campaign.campaign_id}")
    try:
        document = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise DiscoveryError(f"{label}: JSON ilegível") from exc
    require(isinstance(document, dict), "raiz deve ser objeto")
    state = cast(dict[str, object], document)
    v1_keys = {
        "schemaVersion",
        "campaign",
        "scope",
        "attemptNumber",
        "maxPairs",
        "maxRequests",
        "maxSeconds",
        "window",
        "seedsSha256",
        "decisionRecord",
    }
    v2_keys = v1_keys | {
        "campaignName",
        "patch",
        "queues",
        "mapId",
        "manifestSha256",
    }
    schema = state.get("schemaVersion")
    if campaign.is_generic:
        require(schema == 2 and set(state) == v2_keys, "perfil v2/campos divergentes")
        require(
            state["campaignName"] == campaign.campaign_name
            and state["patch"] == campaign.patch
            and state["queues"] == list(campaign.queues)
            and state["mapId"] == campaign.map_id
            and isinstance(manifest_sha256, str)
            and state["manifestSha256"] == manifest_sha256
            and bool(_SHA256_HEX.fullmatch(str(state["manifestSha256"]))),
            "identidade ou hash do manifesto diverge",
        )
    else:
        require(schema == 1 and set(state) == v1_keys, "perfil v1/campos divergentes")
    require(
        schema == (2 if campaign.is_generic else 1)
        and state["campaign"] == campaign.campaign_id
        and state["scope"] == AUTHORIZATION_SCOPE,
        "versão, campanha ou escopo",
    )
    attempt = state["attemptNumber"]
    require(
        not isinstance(attempt, bool) and attempt == attempt_number,
        "tentativa autorizada diverge da solicitada",
    )
    require(
        state["maxPairs"] == campaign.max_pairs
        and state["maxSeconds"] == campaign.max_seconds
        and state["window"] == {"startTime": campaign.window_start, "endTime": campaign.window_end},
        "teto de pares, teto de tempo ou janela divergem dos valores fixados",
    )
    max_requests = state["maxRequests"]
    require(
        isinstance(max_requests, int)
        and not isinstance(max_requests, bool)
        and 1 <= max_requests <= campaign.max_requests,
        "teto de requisições fora do intervalo",
    )
    seeds_sha256 = state["seedsSha256"]
    require(
        isinstance(seeds_sha256, str) and bool(_SHA256_HEX.fullmatch(seeds_sha256)),
        "hash das sementes",
    )
    record = state["decisionRecord"]
    require(isinstance(record, dict) and set(record) == {"path", "sha256"}, "registro da decisão")
    record_map = cast(dict[str, object], record)
    relative, digest = record_map["path"], record_map["sha256"]
    require(
        isinstance(relative, str)
        and bool(relative)
        and not Path(relative).is_absolute()
        and ".." not in Path(relative).parts
        and isinstance(digest, str)
        and bool(_SHA256_HEX.fullmatch(digest)),
        "caminho ou hash do registro da decisão",
    )
    try:
        record_path = private_root / cast(str, relative)
        if any(part.is_symlink() for part in (record_path, *record_path.parents)):
            raise DiscoveryError(f"{label}: registro da decisão contém symlink")
        record_path.resolve().relative_to(private_root.resolve())
        record_bytes = record_path.read_bytes()
    except OSError as exc:
        raise DiscoveryError(f"{label}: registro da decisão ausente") from exc
    except ValueError as exc:
        raise DiscoveryError(f"{label}: registro da decisão fora de .private") from exc
    require(hashlib.sha256(record_bytes).hexdigest() == digest, "registro da decisão alterado")
    return CollectionAuthorization(
        max_requests=cast(int, max_requests),
        seeds_sha256=cast(str, seeds_sha256),
        schema_version=cast(int, schema),
        manifest_sha256=manifest_sha256 if campaign.is_generic else None,
    )


def _json_or_none(body: bytes) -> object:
    try:
        return json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


def _target_mismatch(
    document: object,
    match_id: str,
    campaign: Campaign = CC323,
    *,
    expected_queue: int | None = None,
) -> str | None:
    """Why a match body falls outside the immutable campaign target, or None."""
    if not isinstance(document, dict):
        return "IDENTITY_MISMATCH"
    metadata, info = document.get("metadata"), document.get("info")
    if not isinstance(metadata, dict) or metadata.get("matchId") != match_id:
        return "IDENTITY_MISMATCH"
    if not isinstance(info, dict):
        return "IDENTITY_MISMATCH"
    version = info.get("gameVersion")
    if not isinstance(version, str) or ".".join(version.split(".")[:2]) != campaign.patch:
        return "OTHER_PATCH"
    if info.get("queueId") not in campaign.queues:
        return "OTHER_QUEUE"
    if expected_queue is not None and info.get("queueId") != expected_queue:
        return "LISTED_QUEUE_MISMATCH"
    if info.get("mapId") != campaign.map_id:
        return "OTHER_MAP"
    if info.get("endOfGameResult") != "GameComplete":
        return "NOT_COMPLETE"
    return None


def _participants(
    document: object, scope_champions: frozenset[int] = SCOPE_CHAMPIONS
) -> list[tuple[str, bool]]:
    """(puuid, played the scope) per well-formed participant; malformed entries are skipped."""
    info = document.get("info") if isinstance(document, dict) else None
    entries = info.get("participants") if isinstance(info, dict) else None
    found: list[tuple[str, bool]] = []
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict):
            continue
        puuid = entry.get("puuid")
        if not isinstance(puuid, str) or not puuid or len(puuid) > 256:
            continue
        in_scope = (
            entry.get("championId") in scope_champions
            and entry.get("teamPosition") == SCOPE_POSITION
        )
        found.append((puuid, in_scope))
    return found


def _timeline_identity_matches(body: bytes, match_id: str) -> bool:
    document = _json_or_none(body)
    metadata = document.get("metadata") if isinstance(document, dict) else None
    return isinstance(metadata, dict) and metadata.get("matchId") == match_id


def _complete_pairs(directory: Path) -> set[str]:
    """IDs with both references published in a raw store (never reads the bodies)."""
    pairs: set[str] = set()
    for ref in directory.glob("*.match.ref"):
        match_id = ref.name.removesuffix(".match.ref")
        if _MATCH_ID.fullmatch(match_id) and (directory / f"{match_id}.timeline.ref").exists():
            pairs.add(match_id)
    return pairs


class _Expansion:
    """Best-first snowball: seeds, then players seen in the scope, then everyone else."""

    def __init__(
        self,
        *,
        transport: CollectionTransport,
        store: DurableRawMatchStore,
        stored: set[str],
        known: dict[str, DurableRawMatchStore],
        max_pairs: int,
        before_request: Callable[[], None],
        campaign: Campaign = CC323,
    ) -> None:
        self.campaign = campaign
        self.transport = transport
        self.store = store
        self.stored = stored
        self.known = known
        self.max_pairs = max_pairs
        self.before_request = before_request
        self.counts: Counter[str] = Counter()
        self.queue_stats: dict[int, Counter[str]] = {queue: Counter() for queue in campaign.queues}
        self.new_ids: list[str] = []
        self._queue: list[tuple[int, int, str]] = []
        self._best_priority: dict[str, int] = {}
        self._listed: set[str] = set()
        self._seen_matches: set[str] = set()
        self._order = 0

    @property
    def pair_cap_reached(self) -> bool:
        return len(self.stored) >= self.max_pairs

    def _enqueue(self, puuid: str, priority: int) -> None:
        # A player first seen outside the scope is promoted if later seen inside it.
        if puuid in self._listed or self._best_priority.get(puuid, priority + 1) <= priority:
            return
        self._best_priority[puuid] = priority
        heapq.heappush(self._queue, (priority, self._order, puuid))
        self._order += 1

    async def run(self, seeds: Sequence[Seed]) -> str:
        for seed in seeds:
            self.before_request()
            try:
                puuid = await self.transport.resolve_account_puuid(
                    seed.account.game_name, seed.account.tag_line
                )
            except (SourceNotFoundError, SourceSchemaError):
                self.counts["SEED_ACCOUNT_UNAVAILABLE"] += 1
                continue
            self._enqueue(puuid, _SEED_PRIORITY)
        while self._queue and not self.pair_cap_reached:
            _priority, _order, puuid = heapq.heappop(self._queue)
            if puuid in self._listed:
                continue
            self._listed.add(puuid)
            for queue in self.campaign.queues:
                self.before_request()
                before = self.transport.requests_made
                try:
                    listed = await self.transport.list_match_ids_by_puuid(
                        puuid,
                        queue=queue,
                        start=0,
                        count=self.campaign.listing_count,
                        start_time=self.campaign.window_start,
                        end_time=self.campaign.window_end,
                    )
                except (SourceNotFoundError, SourceSchemaError):
                    self.counts["PLAYER_LISTING_UNAVAILABLE"] += 1
                    self.queue_stats[queue]["playerListingUnavailable"] += 1
                    continue
                finally:
                    self.queue_stats[queue]["listingRequests"] += (
                        self.transport.requests_made - before
                    )
                self.counts["PLAYERS_LISTED"] += 1
                self.queue_stats[queue]["playersListed"] += 1
                self.queue_stats[queue]["matchIdsListed"] += len(listed)
                match_ids = self.campaign.keep(puuid, listed)
                if len(listed) > len(match_ids):
                    discarded = len(listed) - len(match_ids)
                    self.counts["LISTED_IDS_NOT_SAMPLED"] += discarded
                    self.queue_stats[queue]["listedIdsNotSampled"] += discarded
                for match_id in match_ids:
                    if self.pair_cap_reached:
                        break
                    await self._visit(match_id, queue=queue)
        return "PAIR_CAP_REACHED" if self.pair_cap_reached else "QUEUE_EXHAUSTED"

    async def _visit(self, match_id: str, *, queue: int) -> None:
        self.queue_stats[queue]["selectedMatchIds"] += 1
        if match_id in self._seen_matches:
            self.queue_stats[queue]["duplicateMatchIds"] += 1
            return
        self._seen_matches.add(match_id)
        if not _MATCH_ID.fullmatch(match_id):
            self.counts["OUT_OF_PLATFORM_OR_INVALID_ID"] += 1
            return
        self.counts["MATCHES_LISTED"] += 1
        if match_id in self.stored or match_id in self.known:
            self.queue_stats[queue]["knownPairs"] += 1
            self._expand(self._known_document(match_id), new_pair=False)
            return
        self.before_request()
        before = self.transport.requests_made
        try:
            match = await self.transport.fetch_match(match_id)
        except SourceNotFoundError:
            self.counts["MATCH_NOT_FOUND"] += 1
            return
        except SourceSchemaError:
            self.counts["MATCH_SCHEMA_UNSUPPORTED"] += 1
            return
        finally:
            self.queue_stats[queue]["matchRequests"] += self.transport.requests_made - before
        self.counts["MATCHES_FETCHED"] += 1
        self.queue_stats[queue]["matchesFetched"] += 1
        document = _json_or_none(match.body)
        reason = _target_mismatch(
            document,
            match_id,
            self.campaign,
            expected_queue=queue if self.campaign.is_generic else None,
        )
        if reason in {"IDENTITY_MISMATCH", "LISTED_QUEUE_MISMATCH"}:
            self.counts[reason] += 1
            self.queue_stats[queue][reason] += 1
            return  # untrusted or misattributed payload never feeds the expansion
        if reason is not None:
            self.counts[reason] += 1
            self.queue_stats[queue][reason] += 1
            self._expand(document, new_pair=False)
            return
        self.queue_stats[queue]["targetMatches"] += 1
        stored = await self._store_pair(match_id, match, queue=queue)
        self.queue_stats[queue]["newPairs"] += int(stored)
        self._expand(document, new_pair=stored)

    async def _store_pair(self, match_id: str, match: RawRiotResponse, *, queue: int) -> bool:
        self.before_request()
        before = self.transport.requests_made
        try:
            timeline = await self.transport.fetch_timeline(match_id)
        except SourceNotFoundError:
            self.counts["TIMELINE_NOT_FOUND"] += 1
            return False
        except SourceSchemaError:
            self.counts["TIMELINE_SCHEMA_UNSUPPORTED"] += 1
            return False
        finally:
            self.queue_stats[queue]["timelineRequests"] += self.transport.requests_made - before
        if not _timeline_identity_matches(timeline.body, match_id):
            self.counts["TIMELINE_IDENTITY_MISMATCH"] += 1
            self.queue_stats[queue]["TIMELINE_IDENTITY_MISMATCH"] += 1
            return False
        self.store.store(match_id, "match", match.body, match.sha256)
        self.store.store(match_id, "timeline", timeline.body, timeline.sha256)
        self.stored.add(match_id)
        self.new_ids.append(match_id)
        self.counts["PAIRS_STORED"] += 1
        return True

    def _known_document(self, match_id: str) -> object:
        if match_id in self.stored:
            self.counts["PAIR_ALREADY_PRESENT"] += 1
            body = self.store.read_if_valid(match_id, "match")
        else:
            self.counts["PAIR_KNOWN_ELSEWHERE"] += 1
            body = self.known[match_id].read_if_valid_read_only(match_id, "match")
        if body is None:
            raise DiscoveryError("par conhecido sem corpo de partida legível")
        return _json_or_none(body)

    def _expand(self, document: object, *, new_pair: bool) -> None:
        participants = _participants(document, self.campaign.scope_champions)
        if new_pair:
            self.counts["SCOPE_SLOTS_IN_NEW_PAIRS"] += sum(1 for _, scope in participants if scope)
        for puuid, in_scope in participants:
            self._enqueue(puuid, _SCOPE_PRIORITY if in_scope else _OTHER_PRIORITY)


async def run_dataset_collection(
    *,
    transport: CollectionTransport,
    seeds: Sequence[Seed],
    output_dir: Path,
    attempt_number: int,
    authorized_max_requests: int,
    known_raw_directories: Sequence[Path] = (),
    max_pairs: int | None = None,
    max_requests: int | None = None,
    max_seconds: int | None = None,
    campaign: Campaign = CC323,
    campaign_manifest_sha256: str | None = None,
    clock: Callable[[], float] = time.monotonic,
    process_started_monotonic_ns: int | None = None,
    clock_ns: Callable[[], int] = time.monotonic_ns,
) -> CollectionSummary:
    """Collect until the pair, request or time cap, or until no player is left to list.

    Every HTTP attempt is reserved durably before it is sent. Pairs already in
    the campaign store or in `known_raw_directories` (opened read-only) cost no
    request and still feed the expansion. A source failure ends the attempt
    with a partial report; only a local fault raises. Caps default to the campaign's.
    """
    if campaign.is_generic:
        for label, override, ceiling in (
            ("pares", max_pairs, campaign.max_pairs),
            ("requisições", max_requests, campaign.max_requests),
            ("tempo", max_seconds, campaign.max_seconds),
        ):
            if override is not None and (
                isinstance(override, bool)
                or not isinstance(override, int)
                or override < 1
                or override > ceiling
            ):
                raise DiscoveryError(f"override de {label} excede ou invalida o teto do manifesto")
    max_pairs = campaign.max_pairs if max_pairs is None else max_pairs
    max_requests = campaign.max_requests if max_requests is None else max_requests
    max_seconds = campaign.max_seconds if max_seconds is None else max_seconds
    name = campaign.output_dir_name
    if (
        isinstance(attempt_number, bool)
        or not isinstance(attempt_number, int)
        or attempt_number < 1
    ):
        raise DiscoveryError("número de tentativa inválido")
    if (
        isinstance(authorized_max_requests, bool)
        or not isinstance(authorized_max_requests, int)
        or not 1 <= authorized_max_requests <= max_requests
    ):
        raise DiscoveryError("teto autorizado de requisições inválido")
    if not seeds:
        raise DiscoveryError("nenhuma semente consentida; nada foi consultado")
    if transport.requests_made:
        raise DiscoveryError(
            "transporte já emitiu requisições; nova tentativa exige instância limpa"
        )
    if output_dir.name != name or not any(part == ".private" for part in output_dir.parts):
        raise DiscoveryError(f"saída deve ser o diretório privado {name}")
    try:
        output_mode = stat.S_IMODE(output_dir.stat().st_mode)
    except OSError as exc:
        raise DiscoveryError(f"diretório privado {name} ausente") from exc
    if output_mode & 0o077:
        raise DiscoveryError(f"diretório {name} deve usar modo privado")

    if campaign.is_generic:
        if not isinstance(campaign_manifest_sha256, str) or not _SHA256_HEX.fullmatch(
            campaign_manifest_sha256
        ):
            raise DiscoveryError("hash do manifesto v2 ausente")
        manifest_path = output_dir / PATCH_CAMPAIGN_MANIFEST
        raw_manifest, digest = read_private_with_sidecar(manifest_path, "manifesto da campanha")
        if digest != campaign_manifest_sha256:
            raise DiscoveryError("manifesto v2 alterado durante a campanha")
        try:
            stored_manifest = json.loads(raw_manifest)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise DiscoveryError("manifesto v2 ilegível") from exc
        if not isinstance(stored_manifest, dict):
            raise DiscoveryError("manifesto v2 deve ser objeto")
        input_hashes = stored_manifest.get("inputHashes")
        if not isinstance(input_hashes, dict):
            raise DiscoveryError("hashes do manifesto v2 ausentes")
        if input_hashes.get("toolSha256") != effective_campaign_source_sha256():
            raise DiscoveryError("fontes ativas da campanha divergem do manifesto")
        expected = patch_campaign_manifest(
            campaign,
            private_root=private_root_for(output_dir),
            output_dir=output_dir,
            seeds_sha256=cast(str, input_hashes.get("seedsSha256", "")),
            scope_sha256=cast(str, input_hashes.get("scopeSha256", "")),
            tool_sha256=cast(str, input_hashes.get("toolSha256", "")),
        )
        if stored_manifest != expected:
            raise DiscoveryError("manifesto v2 não corresponde à configuração ativa")

    paths = attempt_paths(output_dir, attempt_number)
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
    if campaign.is_generic:
        prior_requests, prior_elapsed_ns = prior_patch_campaign_usage(
            output_dir, attempt_number, campaign_manifest_sha256 or ""
        )
    else:
        prior_requests, prior_elapsed_ns = prior_campaign_usage(output_dir, attempt_number)
    request_limit = min(max_requests - prior_requests, authorized_max_requests)
    if request_limit < 1:
        raise DiscoveryError("teto cumulativo de requisições da campanha já consumido")
    elapsed_before_ns = clock_ns() - process_started
    if elapsed_before_ns < 0:
        raise DiscoveryError("relógio monotônico retrocedeu dentro da mesma execução")
    max_ns = max_seconds * 1_000_000_000
    remaining_ns = max_ns - prior_elapsed_ns - elapsed_before_ns - _SHUTDOWN_RESERVE_NS
    if remaining_ns <= 0:
        raise DiscoveryError("teto cumulativo de tempo da campanha já consumido")
    if any(paths[name].exists() for name in ("report", "marker", "budget")):
        raise DiscoveryError("esta tentativa já foi iniciada ou concluída; revisão necessária")

    raw_directory = paths["raw"]
    try:
        if campaign.is_generic:
            validate_private_campaign_path(
                private_root_for(output_dir), raw_directory, "raiz raw da campanha"
            )
        raw_directory.mkdir(mode=0o700, exist_ok=True)
        raw_directory.chmod(0o700)
        store = DurableRawMatchStore(raw_directory)
        stored = _complete_pairs(raw_directory)
        known: dict[str, DurableRawMatchStore] = {}
        for directory in known_raw_directories:
            if campaign.is_generic:
                private_root = private_root_for(output_dir)
                resolved_private = private_root.resolve()
                if any(part.is_symlink() for part in (directory, *directory.parents)):
                    raise DiscoveryError("raiz raw conhecida contém symlink")
                try:
                    directory.resolve().relative_to(resolved_private)
                except ValueError as exc:
                    raise DiscoveryError("raiz raw conhecida fora de .private") from exc
            known_store = DurableRawMatchStore(directory, create=False)
            for match_id in sorted(_complete_pairs(directory)):
                known.setdefault(match_id, known_store)
    except OSError as exc:
        raise DiscoveryError("armazenamento bruto privado indisponível") from exc
    if len(stored) >= max_pairs:
        raise DiscoveryError("teto de pares da campanha já alcançado")

    lock_handle = (output_dir / f".{name}-collection.lock").open("a+")
    try:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        lock_handle.close()
        raise DiscoveryError(f"há outra execução {campaign.campaign_id} em andamento") from exc
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

    try:
        binding = (
            {
                "campaign": campaign.campaign_id,
                "attemptNumber": attempt_number,
                "manifestSha256": campaign_manifest_sha256,
            }
            if campaign.is_generic
            else None
        )
        with BudgetJournal(paths["budget"], limit=request_limit, binding=binding) as journal:
            local_stops: list[str] = []

            def record_local_budget(request_class: str, budget_kind: str) -> None:
                journal.record_local_budget(request_class, budget_kind)
                local_stops.append(budget_kind)

            transport.set_request_limit(request_limit)
            transport.set_total_time_limit(remaining_ns / 1_000_000_000)
            transport.set_request_observer(journal.reserve)
            transport.set_request_result_observer(journal.record_result)
            transport.set_local_budget_observer(record_local_budget)

            def before_request() -> None:
                elapsed_ns = prior_elapsed_ns + clock_ns() - process_started
                if elapsed_ns + _SHUTDOWN_RESERVE_NS >= max_ns:
                    raise _CapReached("TIME_CAP_REACHED")
                if journal.requests_reserved >= request_limit:
                    raise _CapReached("REQUEST_CAP_REACHED")

            expansion = _Expansion(
                transport=transport,
                store=store,
                stored=stored,
                known=known,
                max_pairs=max_pairs,
                before_request=before_request,
                campaign=campaign,
            )
            journal_status, failure_code = "COMPLETED", None
            try:
                status = await expansion.run(seeds)
            except _CapReached as cap:
                status = cap.status
            except SourceAuthError:
                status = journal_status = "STOPPED_AUTH"
            except SourceError as exc:
                if local_stops:
                    status = _LOCAL_STOPS[local_stops[-1]]
                elif isinstance(exc, SourceRateLimitedError):
                    status, journal_status = "STOPPED_RATE_LIMITED", "STOPPED_RATE_OR_BUDGET"
                else:
                    status, journal_status = "STOPPED_SOURCE_ERROR", "INTERRUPTED"
                    failure_code = exc.code
            except BaseException:
                # Includes Ctrl-C/cancellation: the durable reservations stay
                # countable, so the campaign can resume after review.
                journal.finish(status="INTERRUPTED")
                raise

            counts = expansion.counts
            requests_used = journal.requests_reserved
            elapsed_seconds = round(clock() - started, 3)
            report: dict[str, object] = {
                "schemaVersion": campaign.schema_version,
                "campaign": campaign.campaign_id,
                "attemptNumber": attempt_number,
                "status": status,
                "target": {
                    "patch": campaign.patch,
                    **(
                        {"queueIds": list(campaign.queues)}
                        if campaign.is_generic
                        else {"queueId": campaign.queues[0]}
                    ),
                    "mapId": campaign.map_id,
                    "gameComplete": True,
                },
                "window": {"startTime": campaign.window_start, "endTime": campaign.window_end},
                "expansion": {
                    "source": "CONSENTED_SEEDS_PLUS_ALL_PARTICIPANTS",
                    "maximumParticipantHops": None,
                    (
                        "matchesListedPerPlayerPerQueue"
                        if campaign.is_generic
                        else "matchesListedPerPlayer"
                    ): campaign.kept_per_player,
                    "listingRequestedPerPlayer": campaign.listing_count,
                    "listingSelection": (
                        "MOST_RECENT"
                        if campaign.sample_per_player is None
                        else "SALTED_HASH_SAMPLE"
                    ),
                    "priority": ["SEED", "SCOPE_PLAYER", "OTHER_PLAYER"],
                    "scope": {
                        "teamPosition": SCOPE_POSITION,
                        "championIds": sorted(campaign.scope_champions),
                    },
                    "seedCount": len(seeds),
                },
                "caps": {
                    "maxPairs": max_pairs,
                    "campaignMaxRequests": max_requests,
                    "campaignMaxSeconds": max_seconds,
                    "attemptRequestLimit": request_limit,
                    "priorRequests": prior_requests,
                    "priorProcessSeconds": round(prior_elapsed_ns / 1_000_000_000, 3),
                },
                "pairsStoredTotal": len(stored),
                "newMatchIds": sorted(expansion.new_ids),
                "counts": dict(sorted(counts.items())),
                "requests": requests_used,
                "elapsedSeconds": elapsed_seconds,
                "privacy": {
                    "participantIdentifiersInReport": False,
                    "rawStoredOnlyUnderPrivateDirectory": True,
                    "knownStoresOpenedReadOnly": True,
                    "databaseAccessed": False,
                },
            }
            if campaign.is_generic:
                report["campaignManifestSha256"] = campaign_manifest_sha256
                report["requestCountsByQueue"] = {
                    str(queue): dict(sorted(expansion.queue_stats[queue].items()))
                    for queue in campaign.queues
                }
            try:
                report_sha256 = _atomic_write_once(paths["report"], canonical_json(report))
            except Exception:
                journal.finish(status="INTERRUPTED")
                raise
            journal.finish(status=journal_status, failure_code=failure_code)
            return CollectionSummary(
                status=status,
                pairs_new=len(expansion.new_ids),
                pairs_stored_total=len(stored),
                pairs_known_elsewhere=counts["PAIR_KNOWN_ELSEWHERE"],
                players_listed=counts["PLAYERS_LISTED"],
                matches_listed=counts["MATCHES_LISTED"],
                matches_fetched=counts["MATCHES_FETCHED"],
                scope_slots_in_new_pairs=counts["SCOPE_SLOTS_IN_NEW_PAIRS"],
                discarded={
                    reason: count
                    for reason, count in sorted(counts.items())
                    if reason not in _PROGRESS_COUNTS
                },
                requests=requests_used,
                elapsed_seconds=elapsed_seconds,
                report_path=str(paths["report"]),
                report_sha256=report_sha256,
            )
    finally:
        lock_handle.close()
