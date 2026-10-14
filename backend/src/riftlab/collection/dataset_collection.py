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
import os
import re
import stat
import time
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
from riftlab.collection.cc316_discovery import BudgetJournal, DiscoveryError, _atomic_write_once
from riftlab.collection.seeds import Seed
from riftlab.ingestion.infrastructure.raw_store import DurableRawMatchStore
from riftlab.ingestion.infrastructure.riot_transport import RawRiotResponse
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

    @property
    def kept_per_player(self) -> int:
        return self.listing_count if self.sample_per_player is None else self.sample_per_player

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


def load_collection_authorization(
    path: Path, *, attempt_number: int, campaign: Campaign = CC323
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
    require(
        set(state)
        == {
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
        },
        "campos divergentes",
    )
    require(
        state["schemaVersion"] == 1
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
        record_bytes = (private_root / cast(str, relative)).read_bytes()
    except OSError as exc:
        raise DiscoveryError(f"{label}: registro da decisão ausente") from exc
    require(hashlib.sha256(record_bytes).hexdigest() == digest, "registro da decisão alterado")
    return CollectionAuthorization(
        max_requests=cast(int, max_requests), seeds_sha256=cast(str, seeds_sha256)
    )


def _json_or_none(body: bytes) -> object:
    try:
        return json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


def _target_mismatch(document: object, match_id: str) -> str | None:
    """Why a match body falls outside 16.19 / 420 / map 11 / GameComplete, or None."""
    if not isinstance(document, dict):
        return "IDENTITY_MISMATCH"
    metadata, info = document.get("metadata"), document.get("info")
    if not isinstance(metadata, dict) or metadata.get("matchId") != match_id:
        return "IDENTITY_MISMATCH"
    if not isinstance(info, dict):
        return "IDENTITY_MISMATCH"
    version = info.get("gameVersion")
    if not isinstance(version, str) or ".".join(version.split(".")[:2]) != TARGET_PATCH:
        return "OTHER_PATCH"
    if info.get("queueId") != TARGET_QUEUE:
        return "OTHER_QUEUE"
    if info.get("mapId") != TARGET_MAP:
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
            self.before_request()
            try:
                listed = await self.transport.list_match_ids_by_puuid(
                    puuid,
                    queue=TARGET_QUEUE,
                    start=0,
                    count=self.campaign.listing_count,
                    start_time=self.campaign.window_start,
                    end_time=self.campaign.window_end,
                )
            except (SourceNotFoundError, SourceSchemaError):
                self.counts["PLAYER_LISTING_UNAVAILABLE"] += 1
                continue
            self.counts["PLAYERS_LISTED"] += 1
            match_ids = self.campaign.keep(puuid, listed)
            if len(listed) > len(match_ids):
                self.counts["LISTED_IDS_NOT_SAMPLED"] += len(listed) - len(match_ids)
            for match_id in match_ids:
                if self.pair_cap_reached:
                    break
                await self._visit(match_id)
        return "PAIR_CAP_REACHED" if self.pair_cap_reached else "QUEUE_EXHAUSTED"

    async def _visit(self, match_id: str) -> None:
        if match_id in self._seen_matches:
            return
        self._seen_matches.add(match_id)
        if not _MATCH_ID.fullmatch(match_id):
            self.counts["OUT_OF_PLATFORM_OR_INVALID_ID"] += 1
            return
        self.counts["MATCHES_LISTED"] += 1
        if match_id in self.stored or match_id in self.known:
            self._expand(self._known_document(match_id), new_pair=False)
            return
        self.before_request()
        try:
            match = await self.transport.fetch_match(match_id)
        except SourceNotFoundError:
            self.counts["MATCH_NOT_FOUND"] += 1
            return
        except SourceSchemaError:
            self.counts["MATCH_SCHEMA_UNSUPPORTED"] += 1
            return
        self.counts["MATCHES_FETCHED"] += 1
        document = _json_or_none(match.body)
        reason = _target_mismatch(document, match_id)
        if reason == "IDENTITY_MISMATCH":
            self.counts[reason] += 1
            return  # an untrusted body never feeds the expansion
        if reason is not None:
            self.counts[reason] += 1
            self._expand(document, new_pair=False)
            return
        self._expand(document, new_pair=await self._store_pair(match_id, match))

    async def _store_pair(self, match_id: str, match: RawRiotResponse) -> bool:
        self.before_request()
        try:
            timeline = await self.transport.fetch_timeline(match_id)
        except SourceNotFoundError:
            self.counts["TIMELINE_NOT_FOUND"] += 1
            return False
        except SourceSchemaError:
            self.counts["TIMELINE_SCHEMA_UNSUPPORTED"] += 1
            return False
        if not _timeline_identity_matches(timeline.body, match_id):
            self.counts["TIMELINE_IDENTITY_MISMATCH"] += 1
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
        raw_directory.mkdir(mode=0o700, exist_ok=True)
        raw_directory.chmod(0o700)
        store = DurableRawMatchStore(raw_directory)
        stored = _complete_pairs(raw_directory)
        known: dict[str, DurableRawMatchStore] = {}
        for directory in known_raw_directories:
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
        with BudgetJournal(paths["budget"], limit=request_limit) as journal:
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
                "schemaVersion": 1,
                "campaign": campaign.campaign_id,
                "attemptNumber": attempt_number,
                "status": status,
                "target": {
                    "patch": TARGET_PATCH,
                    "queueId": TARGET_QUEUE,
                    "mapId": TARGET_MAP,
                    "gameComplete": True,
                },
                "window": {"startTime": campaign.window_start, "endTime": campaign.window_end},
                "expansion": {
                    "source": "CONSENTED_SEEDS_PLUS_ALL_PARTICIPANTS",
                    "maximumParticipantHops": None,
                    "matchesListedPerPlayer": campaign.kept_per_player,
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
