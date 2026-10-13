"""Serviço de aplicação de imports (CC-201): orquestra transações curtas
sobre IngestionRepository — nunca abre transação de banco durante rede
(aqui não há rede nenhuma; quem toca rede é o worker). Camada intermediária
entre as rotas HTTP/worker e o repositório, dona da idempotência de
POST /imports, da precondição de retry e do ciclo reserva -> busca -> valida
-> persiste do worker."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from riftlab.config import Settings
from riftlab.ingestion.domain import (
    LEASE_HEARTBEAT_SECONDS,
    ImportJob,
    ImportJobState,
    ImportMode,
    JobKind,
    Match,
    MatchSourceResult,
    Participant,
)
from riftlab.ingestion.infrastructure.demo_source import DemoMatchSource
from riftlab.ingestion.infrastructure.raw_store import DurableRawMatchStore, RawStoreCorruptedError
from riftlab.ingestion.infrastructure.riot_source import LocalRawMatchSource, RiotMatchSource
from riftlab.ingestion.infrastructure.riot_transport import RiotFetchBudget, RiotHttpTransport
from riftlab.ingestion.infrastructure.subject_hash import SubjectHashSecretMissingError
from riftlab.ingestion.repository import SqlAlchemyIngestionRepository
from riftlab.shared.db import unit_of_work
from riftlab.shared.errors import (
    ConflictError,
    NotFoundError,
    SourceAuthError,
    SourceError,
    SourceNotFoundError,
    SourceRateLimitedError,
    SourceSchemaError,
)
from riftlab.timeline.domain import TimelineEvent
from riftlab.timeline.repository import SqlAlchemyTimelineRepository

SessionFactory = async_sessionmaker[AsyncSession]

logger = logging.getLogger("riftlab.worker")


async def request_import(
    session_factory: SessionFactory,
    requested_match_id: str,
    mode: ImportMode,
    _after_initial_read: Callable[[], object] | None = None,
) -> ImportJob:
    """POST /imports: idempotente. Reaproveita o job mais recente da
    identidade se ainda ativo ou já SUCCEEDED (nunca duplica partida/job
    ativo); cria um novo só quando o mais recente é FAILED ou não existe.
    Duas chamadas concorrentes competem no índice único parcial do banco —
    que cobre estados ativos E SUCCEEDED (revisão CC-201, achado 4: sem
    isso, um concorrente terminando SUCCEEDED bem entre esta consulta e o
    create_import_job abaixo não era barrado pelo índice, que só cobria
    QUEUED/RUNNING/RETRY_WAIT). A perdedora nunca recebe 409: reconsulta
    numa transação nova e devolve o job da vencedora. _after_initial_read é
    injetável só para teste (barreira determinística da janela de corrida),
    nunca usado em produção."""
    async with unit_of_work(session_factory) as session:
        repo = SqlAlchemyIngestionRepository(session)
        latest = await repo.get_latest_import_job(requested_match_id, mode)
        if latest is not None and latest.state != ImportJobState.FAILED:
            return latest

    if _after_initial_read is not None:
        result = _after_initial_read()
        if hasattr(result, "__await__"):
            await result

    new_job = ImportJob(
        id=uuid.uuid4(),
        requested_match_id=requested_match_id,
        mode=mode,
        state=ImportJobState.QUEUED,
        retry_of_job_id=latest.id if latest is not None else None,
    )
    try:
        async with unit_of_work(session_factory) as session:
            repo = SqlAlchemyIngestionRepository(session)
            await repo.create_import_job(new_job)
        return new_job
    except ConflictError:
        async with unit_of_work(session_factory) as session:
            repo = SqlAlchemyIngestionRepository(session)
            winner = await repo.get_latest_import_job(requested_match_id, mode)
        if winner is None:  # pragma: no cover - defensivo, não deveria acontecer
            raise
        return winner


async def get_import_job(session_factory: SessionFactory, job_id: uuid.UUID) -> ImportJob:
    async with unit_of_work(session_factory) as session:
        repo = SqlAlchemyIngestionRepository(session)
        job = await repo.get_import_job(job_id)
    if job is None or job.kind not in (JobKind.IMPORT, JobKind.PURGE):  # preparação: rotas próprias
        raise NotFoundError(f"import job {job_id} não encontrado")
    return job


async def retry_import(
    session_factory: SessionFactory,
    job_id: uuid.UUID,
    _after_initial_read: Callable[[], object] | None = None,
) -> ImportJob:
    """POST /imports/{id}/retry: só permitido quando o job alvo está FAILED
    (docs/contracts.md). Cria uma nova tentativa vinculada — histórico do job
    anterior preservado, nunca sobrescrito. Corrida entre dois retries do
    mesmo job, entre um retry e um POST /imports concorrente, ou entre um
    retry de um FAILED antigo e uma tentativa sucessora que já terminou
    SUCCEEDED nesse meio-tempo, é resolvida do mesmo jeito que
    request_import: o índice único cobre estados ativos e SUCCEEDED, então a
    inserção perdedora nunca cria duplicata — reconsulta e devolve a
    vencedora. _after_initial_read é injetável só para teste."""
    async with unit_of_work(session_factory) as session:
        repo = SqlAlchemyIngestionRepository(session)
        target = await repo.get_import_job(job_id)
        if target is None or target.kind != JobKind.IMPORT:
            raise NotFoundError(f"import job {job_id} não encontrado")
        if target.state != ImportJobState.FAILED:
            raise ConflictError(
                f"import job {job_id} não está FAILED (estado atual: "
                f"{target.state.value}); retry só é permitido para jobs falhados"
            )

    if _after_initial_read is not None:
        result = _after_initial_read()
        if hasattr(result, "__await__"):
            await result

    new_job = ImportJob(
        id=uuid.uuid4(),
        requested_match_id=target.requested_match_id,
        mode=target.mode,
        state=ImportJobState.QUEUED,
        retry_of_job_id=target.id,
    )
    try:
        async with unit_of_work(session_factory) as session:
            repo = SqlAlchemyIngestionRepository(session)
            await repo.create_import_job(new_job)
        return new_job
    except ConflictError:
        async with unit_of_work(session_factory) as session:
            repo = SqlAlchemyIngestionRepository(session)
            winner = await repo.get_latest_import_job(target.requested_match_id, target.mode)
        if winner is None:  # pragma: no cover - defensivo, não deveria acontecer
            raise
        return winner


# ---------------------------------------------------------------------------
# Worker: reserva -> busca (fora de transação) -> valida -> persiste
# ---------------------------------------------------------------------------

_MAX_RETRY_DELAY_SECONDS = 15 * 60  # docs/architecture.md: "teto 15 min"


class _LeaseLostDuringPersistError(Exception):
    """Levantada dentro da própria transação de persistência quando
    mark_import_succeeded devolve False (posse perdida entre a reserva e a
    finalização) — força rollback de match/participantes/eventos junto,
    nunca deixando dado persistido sem o job correspondente em SUCCEEDED
    (evitaria uma inconsistência observável: FAILED com a partida já
    existindo). Quem reclamou o lease refaz o trabalho — reaproveitando o
    armazenamento bruto durável, sem nova rede."""


def _transient_retry_delay(attempts: int, retry_after_seconds: float | None) -> float:
    """Backoff exponencial com jitter, teto 15 min (docs/architecture.md).
    Retry-After do servidor nunca é reduzido para caber no backoff — o maior
    dos dois vence, mesmo que isso ultrapasse o teto local."""
    backoff = min(_MAX_RETRY_DELAY_SECONDS, (2 ** max(attempts, 1)) + random.uniform(0, 1))
    if retry_after_seconds is not None:
        return max(backoff, retry_after_seconds)
    return backoff


async def _renew_lease_periodically(
    session_factory: SessionFactory, job_id: uuid.UUID, generation: int
) -> None:
    """Heartbeat durante I/O (docs/architecture.md: 30s) — mantém a posse
    viva enquanto o worker busca/valida a fonte. Cancelada pelo chamador
    assim que a busca termina, sucesso ou falha."""
    while True:
        await asyncio.sleep(LEASE_HEARTBEAT_SECONDS)
        async with unit_of_work(session_factory) as session:
            repo = SqlAlchemyIngestionRepository(session)
            await repo.renew_lease(job_id, generation, datetime.now(UTC))


def _default_riot_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(follow_redirects=False)


def raw_pair_available(raw_store: DurableRawMatchStore, match_id: str) -> bool:
    """True se match E timeline já estão íntegros no armazenamento local (CC-206). Par
    incompleto ou corrompido é False: quem decide o que fazer com ele é a fonte
    (que reporta RAW_STORE_CORRUPTED / RAW_MATCH_NOT_CACHED), nunca uma suposição aqui."""
    try:
        return all(
            raw_store.read_if_valid(match_id, kind) is not None for kind in ("match", "timeline")
        )
    except (RawStoreCorruptedError, ValueError):
        return False


async def _load_from_source(
    job: ImportJob,
    raw_store: DurableRawMatchStore,
    settings: Settings,
    riot_client_factory: Callable[[], httpx.AsyncClient],
    *,
    local: bool = False,
) -> MatchSourceResult:
    if job.mode == ImportMode.DEMO:
        return await DemoMatchSource().load_match(job.requested_match_id)
    if local:
        # Raw local completo (ou `local_only`): a fonte é estruturalmente incapaz de rede,
        # não constrói transporte nem exige chave (CC-206: importar o que foi coletado
        # SEM buscar de novo).
        return await LocalRawMatchSource(raw_store, settings.subject_hash_secret).load_match(
            job.requested_match_id
        )

    client = riot_client_factory()
    try:
        transport = RiotHttpTransport(
            api_key=settings.riot_api_key or "",
            # max_attempts_per_request=1: o job é o único dono do retry
            # transitório (ver módulo riot_source) — nenhum sleep/retry em
            # memória aqui, o erro surge imediato para retry_at durável.
            budget=RiotFetchBudget(
                max_matches=1,
                max_requests=10,
                max_attempts_per_request=1,
                max_total_seconds=30.0,
            ),
            client=client,
        )
        source = RiotMatchSource(transport, raw_store, settings.subject_hash_secret)
        return await source.load_match(job.requested_match_id)
    finally:
        await client.aclose()


async def _finalize_failed(
    session_factory: SessionFactory, job: ImportJob, error_code: str
) -> None:
    async with unit_of_work(session_factory) as session:
        repo = SqlAlchemyIngestionRepository(session)
        await repo.mark_import_failed(job.id, job.lease_generation, error_code)


async def _finalize_deferred_by_cooldown(
    session_factory: SessionFactory, job: ImportJob, retry_at: datetime
) -> None:
    async with unit_of_work(session_factory) as session:
        repo = SqlAlchemyIngestionRepository(session)
        await repo.mark_import_deferred_by_cooldown(job.id, job.lease_generation, retry_at)


async def _finalize_retry_wait_at(
    session_factory: SessionFactory,
    job: ImportJob,
    error_code: str,
    retry_at: datetime,
) -> None:
    async with unit_of_work(session_factory) as session:
        repo = SqlAlchemyIngestionRepository(session)
        await repo.mark_import_retry_wait(job.id, job.lease_generation, retry_at, error_code)


async def _get_riot_cooldown_until(session_factory: SessionFactory) -> datetime | None:
    async with unit_of_work(session_factory) as session:
        repo = SqlAlchemyIngestionRepository(session)
        return await repo.get_riot_cooldown_until()


async def _extend_riot_cooldown_until(session_factory: SessionFactory, until: datetime) -> None:
    async with unit_of_work(session_factory) as session:
        repo = SqlAlchemyIngestionRepository(session)
        await repo.extend_riot_cooldown_until(until)


async def _persist_result(
    session_factory: SessionFactory, job: ImportJob, result: MatchSourceResult
) -> None:
    """Transação única e curta: match+participantes+eventos+SUCCEEDED juntos,
    ou nada (docs/architecture.md, plano do CC-201: "falha parcial não libera
    partida"). existing != None é a recuperação idempotente depois de um
    crash anterior a este ponto (match já persistido por uma execução
    passada, raw_store já tinha os bytes — sem nova rede); nunca re-insere."""
    async with unit_of_work(session_factory) as session:
        ingestion_repo = SqlAlchemyIngestionRepository(session)
        timeline_repo = SqlAlchemyTimelineRepository(session)

        existing = await ingestion_repo.get_match(result.match_id)
        if existing is None:
            await ingestion_repo.add_match(
                Match(
                    match_id=result.match_id,
                    queue=result.queue,
                    map_id=result.map_id,
                    patch=result.patch,
                    start_at=result.start_at,
                    duration_seconds=result.duration_seconds,
                    raw_hash=result.raw_hash,
                )
            )
            # CC-103 (docs/domain-model.md): sem relationship() do ORM, o
            # flush explícito entre agregados garante a ordem de INSERT que
            # as FKs exigem.
            await session.flush()
            for participant in result.participants:
                await ingestion_repo.add_participant(
                    Participant(
                        match_id=result.match_id,
                        participant_id=participant.participant_id,
                        champion_id=participant.champion_id,
                        team=participant.team,
                        subject_hash=participant.subject_hash,
                        role=participant.role,
                        score=participant.score,
                    )
                )
            for event in result.events:
                await timeline_repo.add_event(
                    TimelineEvent(
                        match_id=result.match_id,
                        source_index=event.source_index,
                        timestamp_ms=event.timestamp_ms,
                        type=event.type,
                        payload=event.payload,
                    )
                )
            await session.flush()

        ok = await ingestion_repo.mark_import_succeeded(
            job.id, job.lease_generation, result.match_id
        )
        if not ok:
            raise _LeaseLostDuringPersistError(f"posse do job {job.id} perdida antes de finalizar")
        # CC-205: a preparação (catálogo -> reconstrução -> extração) é
        # enfileirada na MESMA transação do sucesso da importação — tudo ou
        # nada: nunca há partida importada sem preparação registrada, e nunca
        # uma preparação de partida que não foi importada.
        await ingestion_repo.enqueue_preparation(result.match_id, job.mode)


async def process_next_job(
    session_factory: SessionFactory,
    raw_store: DurableRawMatchStore,
    settings: Settings,
    riot_client_factory: Callable[[], httpx.AsyncClient] | None = None,
    *,
    local_only: bool = False,
) -> bool:
    """Um ciclo do worker. True se um job foi processado (mesmo que tenha
    falhado) — o chamador não espera antes de tentar o próximo; False se não
    havia trabalho elegível agora — o chamador espera o intervalo de poll.
    riot_client_factory é injetável só para teste (httpx.MockTransport, sem
    rede real); cc worker nunca passa isto, usa sempre o cliente real.

    CC-206 — "local primeiro": um job RIOT cujo par match+timeline já está íntegro no
    armazenamento local (coletado antes) é importado por `LocalRawMatchSource`, sem
    transporte, sem chave e sem esperar o cooldown de rede (ele nunca toca a rede).
    `local_only=True` (usado pela CLI de importação da coleta) vai além: NUNCA busca; sem
    o raw completo o job falha de forma permanente com RAW_MATCH_NOT_CACHED."""
    async with unit_of_work(session_factory) as session:
        repo = SqlAlchemyIngestionRepository(session)
        job = await repo.reserve_next_job(datetime.now(UTC))
    if job is None:
        return False

    local = job.mode == ImportMode.RIOT and (
        local_only or raw_pair_available(raw_store, job.requested_match_id)
    )
    if job.mode == ImportMode.RIOT and not local:
        # Cooldown compartilhado entre TODOS os jobs RIOT (achado real da
        # revisão CC-201: um 429 só adiava o job atual; a fila continuava
        # chamando a API com outros matchId). Nunca toca rede nem constrói
        # transporte enquanto o cooldown estiver em vigor — DEMO nunca passa
        # por aqui (mode != RIOT), nunca para por causa disto.
        cooldown_until = await _get_riot_cooldown_until(session_factory)
        if cooldown_until is not None and cooldown_until > datetime.now(UTC):
            # Nunca conta como tentativa transitória (achado real da revisão
            # CC-201): este job nunca chegou a tocar rede, só foi adiado
            # pelo cooldown compartilhado de outro job.
            await _finalize_deferred_by_cooldown(session_factory, job, cooldown_until)
            return True

    heartbeat_task = asyncio.create_task(
        _renew_lease_periodically(session_factory, job.id, job.lease_generation)
    )
    try:
        result = await _load_from_source(
            job, raw_store, settings, riot_client_factory or _default_riot_client, local=local
        )
    except SubjectHashSecretMissingError:
        await _finalize_failed(session_factory, job, "SUBJECT_HASH_SECRET_MISSING")
        return True
    except (SourceAuthError, SourceNotFoundError, SourceSchemaError) as exc:
        await _finalize_failed(session_factory, job, exc.code)
        return True
    except SourceRateLimitedError as exc:
        retry_at = datetime.now(UTC) + timedelta(
            seconds=_transient_retry_delay(job.attempts, exc.retry_after_seconds)
        )
        if job.mode == ImportMode.RIOT:
            # A mesma espera que se aplica a este job vira o cooldown
            # compartilhado — é o mesmo limite de taxa upstream sendo
            # respeitado, não um limite por job.
            await _extend_riot_cooldown_until(session_factory, retry_at)
        await _finalize_retry_wait_at(session_factory, job, exc.code, retry_at)
        return True
    except SourceError as exc:
        retry_at = datetime.now(UTC) + timedelta(seconds=_transient_retry_delay(job.attempts, None))
        await _finalize_retry_wait_at(session_factory, job, exc.code, retry_at)
        return True
    finally:
        heartbeat_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await heartbeat_task

    try:
        await _persist_result(session_factory, job, result)
    except _LeaseLostDuringPersistError:
        logger.warning("worker perdeu a posse do job %s antes de persistir com sucesso", job.id)
    return True
