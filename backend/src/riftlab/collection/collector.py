"""Coletor incremental de partidas (CC-206).

Por candidato (matchId listado só a partir da lista permitida de sementes):

    já no manifesto?  -> duplicata (0 requisição)
    raw local íntegro -> reaproveita (0 requisição, contado)
    senão busca a partida -> filtros (perdas contadas, nada gravado) -> busca a
    timeline -> valida o PAR com o mesmo parser do importador -> grava match e timeline
    no armazenamento durável -> SÓ ENTÃO marca FETCHED no manifesto.

Por isso um crash entre o fetch e a gravação nunca deixa partida analisável: a
retomada reaproveita o que já está no raw (sem repetir requisição) e o par incompleto
nunca chega ao manifesto como FETCHED. Nada aqui grava em banco (a importação é
outro passo, a partir do raw local, ver importing.py).

Retomada: cada passada por semente recomeça da posição 0 da listagem (que cresce pela
cabeça: um índice guardado pularia partidas novas). O progresso é o conjunto de ids
conhecidos do manifesto; conhecida custa 0 requisição, só a página de listagem é paga.

Paradas seguras, todas retomáveis sem duplicar e sem zerar orçamento (toda tentativa
conta, inclusive as de retry do transporte):
- 401/403: registra `authBlockedAt`; a próxima execução RECUSA sem a confirmação
  explícita de que a chave foi trocada (nada derivado da chave é gravado);
- 429: registra o cooldown (Retry-After) e as execuções seguintes recusam antes de
  qualquer requisição; o limitador global vale para todas as chamadas;
- tetos de requisições, partidas novas e duração; teto por semente para equilibrar;
- instabilidade do servidor (falhas transitórias seguidas)."""

from __future__ import annotations

import asyncio
import hashlib
import time
import uuid
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from functools import partial
from typing import Any, TypeVar

import httpx

from riftlab.collection.filters import (
    MatchFilterConfig,
    evaluate_match_document,
    evaluate_match_id,
)
from riftlab.collection.manifest import (
    MAX_TRANSIENT_FAILURES_PER_MATCH,
    CollectionManifest,
    SeedCursor,
)
from riftlab.collection.seeds import Seed
from riftlab.ingestion.infrastructure.raw_store import DurableRawMatchStore, RawStoreCorruptedError
from riftlab.ingestion.infrastructure.riot_rate_limiter import RiotRateLimiter
from riftlab.ingestion.infrastructure.riot_source import parse_raw_pair
from riftlab.ingestion.infrastructure.riot_transport import RiotFetchBudget, RiotHttpTransport
from riftlab.ingestion.infrastructure.subject_hash import SubjectHashSecretMissingError
from riftlab.shared.errors import (
    SourceAuthError,
    SourceError,
    SourceNotFoundError,
    SourceRateLimitedError,
    SourceSchemaError,
)

T = TypeVar("T")

# 429 sem Retry-After: espera desconhecida. Cooldown conservador (a maior janela da
# chave Development documentada no portal, 2 min) em vez de tentar de novo já.
DEFAULT_COOLDOWN_SECONDS = 120.0


class StopReason(StrEnum):
    COMPLETED = "COMPLETED"  # todas as sementes fecharam a passada
    MAX_MATCHES_PER_SEED = "MAX_MATCHES_PER_SEED"  # teto por semente atingido (não fechou)
    PARTIAL = "PARTIAL"  # alguma semente foi pulada ou travou por falha transitória
    MAX_MATCHES = "MAX_MATCHES"
    MAX_REQUESTS = "MAX_REQUESTS"
    MAX_DURATION = "MAX_DURATION"
    AUTH_FAILED = "AUTH_FAILED"  # 401/403 nesta execução
    AUTH_BLOCKED = "AUTH_BLOCKED"  # nem começou: falta confirmar a troca da chave
    RATE_LIMITED = "RATE_LIMITED"
    COOLDOWN_ACTIVE = "COOLDOWN_ACTIVE"  # nem começou: cooldown vigente
    UPSTREAM_UNSTABLE = "UPSTREAM_UNSTABLE"
    INTERRUPTED = "INTERRUPTED"


@dataclass(frozen=True, slots=True)
class CollectionCeilings:
    """Tetos POR EXECUÇÃO. Todos obrigatórios: não existe coleta sem teto."""

    max_requests: int
    max_matches: int
    max_duration_seconds: float
    max_matches_per_seed: int | None = None
    max_consecutive_failures: int = 5
    page_size: int = 100
    max_attempts_per_request: int = 3
    request_timeout_seconds: float = 10.0

    def __post_init__(self) -> None:
        if self.max_requests < 1 or self.max_matches < 1 or self.max_duration_seconds <= 0:
            raise ValueError("tetos de requisições, partidas e duração devem ser positivos")
        if self.max_matches_per_seed is not None and self.max_matches_per_seed < 1:
            raise ValueError("max_matches_per_seed deve ser positivo")
        if not 1 <= self.page_size <= 100:
            raise ValueError("page_size deve estar entre 1 e 100")


def build_collection_transport(
    api_key: str,
    ceilings: CollectionCeilings,
    *,
    client: httpx.AsyncClient | None = None,
    rate_limiter: RiotRateLimiter | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> RiotHttpTransport:
    """Um transporte NOVO por execução: o orçamento de tempo e de requisições do
    transporte é o teto desta execução."""
    return RiotHttpTransport(
        api_key=api_key,
        budget=RiotFetchBudget(
            max_matches=ceilings.max_matches,
            max_requests=ceilings.max_requests,
            max_attempts_per_request=ceilings.max_attempts_per_request,
            max_total_seconds=ceilings.max_duration_seconds,
            request_timeout_seconds=ceilings.request_timeout_seconds,
        ),
        client=client,
        sleep=sleep,
        rate_limiter=rate_limiter if rate_limiter is not None else RiotRateLimiter(sleep=sleep),
    )


@dataclass(slots=True)
class SeedRunStats:
    listed: int = 0
    duplicates: int = 0
    new_valid: int = 0
    excluded: int = 0
    # a semente foi PULADA nesta execução (conta/listagem não resolvida): código seguro
    error: str | None = None


@dataclass(slots=True)
class CollectionRunReport:
    run_id: str
    started_at: datetime
    ended_at: datetime | None = None
    stop_reason: str | None = None
    requests: int = 0
    raw_reused: int = 0
    listed: int = 0
    duplicates: int = 0
    new_valid: int = 0
    excluded: Counter[str] = field(default_factory=Counter)
    transient_failures: int = 0
    limiter_wait_seconds: float = 0.0
    cooldown_seconds_remaining: float | None = None
    per_seed: dict[str, SeedRunStats] = field(default_factory=dict)
    # Comportamento REAL observado (headers de limite, status por tipo de chamada,
    # tamanho de página): diagnóstico agregado, sem identificador.
    observed: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        """Só contagens, rótulos pseudônimos e datas: nenhum dado pessoal."""
        return {
            "runId": self.run_id,
            "startedAt": self.started_at.isoformat(),
            "endedAt": self.ended_at.isoformat() if self.ended_at else None,
            "stopReason": self.stop_reason,
            "requests": self.requests,
            "rawReused": self.raw_reused,
            "listed": self.listed,
            "duplicates": self.duplicates,
            "newValid": self.new_valid,
            "excluded": dict(sorted(self.excluded.items())),
            "excludedTotal": sum(self.excluded.values()),
            "transientFailures": self.transient_failures,
            "limiterWaitSeconds": round(self.limiter_wait_seconds, 3),
            "cooldownSecondsRemaining": self.cooldown_seconds_remaining,
            "observed": self.observed,
            "perSeed": {
                label: {
                    "listed": stats.listed,
                    "duplicates": stats.duplicates,
                    "newValid": stats.new_valid,
                    "excluded": stats.excluded,
                    "error": stats.error,
                }
                for label, stats in sorted(self.per_seed.items())
            },
        }


class _Stop(Exception):
    def __init__(self, reason: StopReason) -> None:
        super().__init__(reason.value)
        self.reason = reason


class _NotFound(Exception):
    pass


class _Invalid(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class _Transient(Exception):
    pass


class _SkipSeed(Exception):
    """A conta/listagem desta semente não pôde ser resolvida agora: pula só ela."""


class _Outcome(StrEnum):
    DONE = "DONE"  # processada (gravada, duplicada ou excluída): passa para a próxima
    STALLED = "STALLED"  # falha transitória: fora do manifesto, volta na próxima passada


async def run_collection(
    *,
    transport: RiotHttpTransport,
    raw_store: DurableRawMatchStore,
    manifest: CollectionManifest,
    seeds: list[Seed],
    filters: MatchFilterConfig,
    subject_hash_secret: str | None,
    ceilings: CollectionCeilings,
    rate_limiter: RiotRateLimiter | None = None,
    resume_after_auth_failure: bool = False,
    start_time: int | None = None,
    end_time: int | None = None,
    clock: Callable[[], float] = time.monotonic,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> CollectionRunReport:
    """Uma execução. Recusa ANTES de qualquer requisição (e antes de tocar o manifesto)
    quando falta o segredo de hash: o validador do par precisa dele e ele nunca é
    gerado nem substituído."""
    if not subject_hash_secret:
        raise SubjectHashSecretMissingError(
            "RIFTLAB_SUBJECT_HASH_SECRET não configurado; a coleta real valida cada par "
            "com o mesmo parser do importador e não roda sem ele"
        )
    if not seeds:
        raise ValueError("a lista permitida de sementes está vazia")
    collector = _Collector(
        transport=transport,
        raw_store=raw_store,
        manifest=manifest,
        seeds=seeds,
        filters=filters,
        secret=subject_hash_secret,
        ceilings=ceilings,
        rate_limiter=rate_limiter,
        resume_after_auth_failure=resume_after_auth_failure,
        start_time=start_time,
        end_time=end_time,
        clock=clock,
        now=now,
    )
    with manifest.exclusive():
        return await collector.run()


class _Collector:
    def __init__(
        self,
        *,
        transport: RiotHttpTransport,
        raw_store: DurableRawMatchStore,
        manifest: CollectionManifest,
        seeds: list[Seed],
        filters: MatchFilterConfig,
        secret: str,
        ceilings: CollectionCeilings,
        rate_limiter: RiotRateLimiter | None,
        resume_after_auth_failure: bool,
        start_time: int | None,
        end_time: int | None,
        clock: Callable[[], float],
        now: Callable[[], datetime],
    ) -> None:
        self._transport = transport
        self._raw = raw_store
        self._manifest = manifest
        self._seeds = seeds
        self._filters = filters
        self._secret = secret
        self._ceilings = ceilings
        self._limiter = rate_limiter
        self._resume_after_auth_failure = resume_after_auth_failure
        self._start_time = start_time
        self._end_time = end_time
        self._clock = clock
        self._now = now
        self._started = clock()
        self._accounted_requests = 0
        self._consecutive_failures = 0
        self._capped = False
        self._incomplete = False
        self._max_page_length = 0
        self._report = CollectionRunReport(run_id=uuid.uuid4().hex[:12], started_at=now())

    # ---- execução ------------------------------------------------------------

    async def run(self) -> CollectionRunReport:
        reason: StopReason | None = None
        try:
            reason = self._precondition_stop()
            if reason is None:
                try:
                    for seed in self._seeds:
                        await self._run_seed(seed)
                    reason = self._end_reason()
                except _Stop as stop:
                    reason = stop.reason
        except BaseException:
            reason = StopReason.INTERRUPTED
            raise
        finally:
            self._finish(reason or StopReason.INTERRUPTED)
        return self._report

    def _end_reason(self) -> StopReason:
        """COMPLETED só quando TODAS as sementes fecharam a passada; senão diz por quê."""
        if self._capped:
            return StopReason.MAX_MATCHES_PER_SEED
        if self._incomplete:
            return StopReason.PARTIAL
        return StopReason.COMPLETED

    def _precondition_stop(self) -> StopReason | None:
        if self._manifest.auth_blocked_at is not None:
            if not self._resume_after_auth_failure:
                return StopReason.AUTH_BLOCKED
            self._manifest.set_auth_blocked_at(None)  # troca manual da chave confirmada
            self._save()
        cooldown = self._manifest.cooldown_until
        current = self._now()
        if cooldown is not None and cooldown > current:
            self._report.cooldown_seconds_remaining = round((cooldown - current).total_seconds(), 1)
            return StopReason.COOLDOWN_ACTIVE
        return None

    def _finish(self, reason: StopReason) -> None:
        report = self._report
        report.stop_reason = reason.value
        report.ended_at = self._now()
        report.requests = self._transport.requests_made
        if self._limiter is not None:
            report.limiter_wait_seconds = self._limiter.waited_seconds
        report.observed = {
            "statusByKind": self._transport.status_counts(),
            "requestedPageSize": self._ceilings.page_size,
            "maxListPageLength": self._max_page_length,
            "timeWindow": {"startTime": self._start_time, "endTime": self._end_time},
            "rateLimiter": self._limiter.snapshot() if self._limiter is not None else None,
        }
        self._manifest.append_run(report.as_dict())
        self._save()

    def _save(self) -> None:
        """Toda gravação leva junto o consumo de requisições até agora: um crash duro
        perde no máximo as da candidata em voo."""
        used = self._transport.requests_made
        self._manifest.add_requests(used - self._accounted_requests)
        self._accounted_requests = used
        self._manifest.save()

    # ---- por semente ---------------------------------------------------------

    async def _run_seed(self, seed: Seed) -> None:
        stats = self._report.per_seed.setdefault(seed.label, SeedRunStats())
        try:
            await self._collect_seed(seed, stats)
        except _SkipSeed:
            self._incomplete = True  # o motivo já está em stats.error; as demais seguem

    async def _seed_call(
        self, stats: SeedRunStats, code: str, call: Callable[[], Awaitable[T]]
    ) -> T:
        """Chamada de conta/listagem: 404, resposta inválida e falha transitória viram
        'semente pulada' (com código seguro no relatório), nunca uma exceção solta."""
        try:
            return await self._call(call)
        except (_NotFound, _Invalid) as exc:
            stats.error = code if isinstance(exc, _NotFound) else exc.code
        except _Transient:
            stats.error = "UPSTREAM_UNAVAILABLE"
            self._report.transient_failures += 1
            self._consecutive_failures += 1
            if self._consecutive_failures >= self._ceilings.max_consecutive_failures:
                raise _Stop(StopReason.UPSTREAM_UNSTABLE) from None
        raise _SkipSeed()

    async def _collect_seed(self, seed: Seed, stats: SeedRunStats) -> None:
        cursor = self._manifest.cursor(seed.label)
        self._check_ceilings(needed=2)  # a conta + ao menos uma página
        puuid = await self._seed_call(
            stats,
            "SEED_ACCOUNT_NOT_FOUND",
            lambda: self._transport.resolve_account_puuid(
                seed.account.game_name, seed.account.tag_line
            ),
        )
        passes = cursor.passes_completed
        # A passada recomeça SEMPRE da posição 0 (ver SeedCursor): as partidas já
        # conhecidas passam por `_process` como duplicadas, a custo zero de requisição, e
        # só as novas são baixadas. Enquanto não chegar ao fim da listagem a passada está
        # aberta (gravada por `_finish`, mesmo que um teto interrompa a execução).
        self._manifest.set_cursor(seed.label, SeedCursor(passes_completed=passes, exhausted=False))
        position = 0  # só pagina DENTRO desta execução; nunca é guardada
        seed_new = 0
        while True:
            self._check_ceilings(needed=1)
            page = await self._seed_call(
                stats, "MATCH_LIST_NOT_FOUND", partial(self._list_page, puuid, position)
            )
            self._max_page_length = max(self._max_page_length, len(page))
            if not page:  # fim real da lista: fecha a passada; a próxima recomeça em 0
                self._manifest.set_cursor(
                    seed.label, SeedCursor(passes_completed=passes + 1, exhausted=True)
                )
                self._save()
                return
            for match_id in page:
                cap = self._ceilings.max_matches_per_seed
                if cap is not None and seed_new >= cap:
                    self._capped = True
                    return
                self._check_ceilings(needed=0)
                stats.listed += 1
                self._report.listed += 1
                before = stats.new_valid
                outcome = await self._process(seed, puuid, match_id, stats)
                if outcome is _Outcome.STALLED:
                    self._incomplete = True  # não entra no manifesto: volta na próxima passada
                    return
                seed_new += stats.new_valid - before
            position += len(page)

    async def _list_page(self, puuid: str, position: int) -> list[str]:
        return await self._transport.list_match_ids_by_puuid(
            puuid,
            queue=self._filters.queue,
            start=position,
            count=self._ceilings.page_size,
            start_time=self._start_time,
            end_time=self._end_time,
        )

    # ---- por candidata ---------------------------------------------------------

    async def _process(
        self, seed: Seed, puuid: str, match_id: str, stats: SeedRunStats
    ) -> _Outcome:
        self._game_date: str | None = None
        if self._manifest.status_of(match_id) is not None:
            stats.duplicates += 1
            self._report.duplicates += 1
            return _Outcome.DONE
        platform_loss = evaluate_match_id(match_id)  # antes de tocar disco ou rede
        if platform_loss is not None:
            return self._exclude(seed, match_id, platform_loss.value, stats)

        try:
            match_body = self._read_raw(match_id, "match")
            timeline_body = self._read_raw(match_id, "timeline")
        except RawStoreCorruptedError:
            return self._exclude(seed, match_id, "RAW_STORE_CORRUPTED", stats)

        try:
            if match_body is None:
                self._check_ceilings(needed=2)  # partida + timeline: nunca gasta 1 à toa
                match_body = (await self._call(lambda: self._transport.fetch_match(match_id))).body
            else:
                self._report.raw_reused += 1
            self._consecutive_failures = 0

            verdict = evaluate_match_document(match_body, self._filters, puuid)
            self._game_date = verdict.game_date
            if not verdict.accepted:
                assert verdict.reason is not None
                return self._exclude(seed, match_id, verdict.reason.value, stats)

            if timeline_body is None:
                self._check_ceilings(needed=1)
                timeline_body = (
                    await self._call(lambda: self._transport.fetch_timeline(match_id))
                ).body
            else:
                self._report.raw_reused += 1
            self._consecutive_failures = 0
        except _NotFound:
            return self._exclude(seed, match_id, "RIOT_NOT_FOUND", stats)
        except _Invalid as invalid:
            return self._exclude(seed, match_id, invalid.code, stats)
        except _Transient:
            return self._transient(seed, match_id, stats)

        try:
            parse_raw_pair(match_id, match_body, timeline_body, self._secret)
        except SourceSchemaError as exc:
            return self._exclude(seed, match_id, exc.code, stats)

        try:
            self._store(match_id, "match", match_body)
            self._store(match_id, "timeline", timeline_body)
        except RawStoreCorruptedError:
            return self._exclude(seed, match_id, "RAW_STORE_CONFLICT", stats)

        assert verdict.patch is not None
        self._manifest.mark_fetched(
            match_id,
            seed=seed.label,
            patch=verdict.patch,
            run_id=self._report.run_id,
            at=self._now(),
            game_date=self._game_date,
        )
        stats.new_valid += 1
        self._report.new_valid += 1
        self._save()
        return _Outcome.DONE

    def _read_raw(self, match_id: str, kind: str) -> bytes | None:
        return self._raw.read_if_valid(match_id, kind)

    def _store(self, match_id: str, kind: str, body: bytes) -> None:
        self._raw.store(match_id, kind, body, hashlib.sha256(body).hexdigest())

    def _exclude(self, seed: Seed, match_id: str, reason: str, stats: SeedRunStats) -> _Outcome:
        self._manifest.mark_excluded(
            match_id,
            reason=reason,
            seed=seed.label,
            run_id=self._report.run_id,
            game_date=self._game_date,
        )
        self._report.excluded[reason] += 1
        stats.excluded += 1
        self._save()
        return _Outcome.DONE

    def _transient(self, seed: Seed, match_id: str, stats: SeedRunStats) -> _Outcome:
        failures = self._manifest.record_transient_failure(match_id)
        self._report.transient_failures += 1
        self._consecutive_failures += 1
        if failures >= MAX_TRANSIENT_FAILURES_PER_MATCH:
            # a mesma partida falhou em várias execuções: não trava a semente para sempre
            return self._exclude(seed, match_id, "UPSTREAM_PERSISTENT_ERROR", stats)
        self._save()
        if self._consecutive_failures >= self._ceilings.max_consecutive_failures:
            raise _Stop(StopReason.UPSTREAM_UNSTABLE)
        return _Outcome.STALLED

    # ---- tetos e erros ---------------------------------------------------------

    def _check_ceilings(self, *, needed: int) -> None:
        """`needed` = requisições que o próximo passo pode gastar (0 = só confere os tetos
        de partidas e duração)."""
        if self._report.new_valid >= self._ceilings.max_matches:
            raise _Stop(StopReason.MAX_MATCHES)
        if self._clock() - self._started >= self._ceilings.max_duration_seconds:
            raise _Stop(StopReason.MAX_DURATION)
        if needed and self._transport.requests_made + needed > self._ceilings.max_requests:
            raise _Stop(StopReason.MAX_REQUESTS)

    async def _call(self, call: Callable[[], Awaitable[T]]) -> T:
        try:
            return await call()
        except SourceAuthError as exc:
            self._manifest.set_auth_blocked_at(self._now())
            self._save()
            raise _Stop(StopReason.AUTH_FAILED) from exc
        except SourceRateLimitedError as exc:
            raise _Stop(self._on_rate_limited(exc)) from exc
        except SourceNotFoundError as exc:
            raise _NotFound() from exc
        except SourceSchemaError as exc:
            raise _Invalid(exc.code) from exc
        except SourceError as exc:
            if self._clock() - self._started >= self._ceilings.max_duration_seconds:
                raise _Stop(StopReason.MAX_DURATION) from exc
            raise _Transient() from exc

    def _on_rate_limited(self, exc: SourceRateLimitedError) -> StopReason:
        if exc.retry_after_seconds is None and (
            self._transport.requests_made >= self._ceilings.max_requests
        ):
            return StopReason.MAX_REQUESTS  # o teto LOCAL de requisições, não um 429 real
        wait = (
            exc.retry_after_seconds
            if exc.retry_after_seconds is not None
            else DEFAULT_COOLDOWN_SECONDS
        )
        self._manifest.set_cooldown_until(self._now() + timedelta(seconds=wait))
        self._save()
        return StopReason.RATE_LIMITED
