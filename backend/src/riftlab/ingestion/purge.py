"""CC-603: expurgo de uma partida e de tudo que deriva dela; retenção com dry-run.

`DELETE /api/v1/matches/{id}` exige a confirmação explícita (o próprio id) e cria um job
PURGE na fila durável (lease, retomada, nova tentativa), consultável em `/imports/{jobId}`.
O worker apaga primeiro os brutos (idempotente: rodar de novo não faz mal) e depois, numa
transação única:

- todas as linhas dependentes, em ordem de chave estrangeira: julgamentos, tentativas,
  análises, pontos e extrações de decisão, estados, reconstruções, eventos, jobs da partida,
  participantes e a partida;
- invalida os datasets registrados que listam a partida e tira de ACTIVE os modelos
  treinados com eles (evento INVALIDATE); o registro recusa promovê-los de novo;
- grava a lápide (HMAC do id, sem o id) e troca o id da partida na linha do próprio job.

Remover linhas não desfaz o que um modelo já treinado aprendeu: por isso o modelo sai de
ativo até retreino e revalidação, e o critério de popularidade versionado em `configs/` não é
recontado sozinho. A retenção (`cc retention`) só lista por padrão; apagar exige `--execute`.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import json
import logging
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from riftlab.analyses.models import AnalysisModel
from riftlab.config import Settings
from riftlab.decisions.models import DecisionExtractionModel, DecisionPointModel
from riftlab.history_sync.models import HistorySyncRunModel
from riftlab.ingestion.domain import (
    ACTIVE_IMPORT_JOB_STATES,
    MAX_TRANSIENT_ATTEMPTS,
    ImportJob,
    ImportJobState,
    ImportMode,
    JobKind,
)
from riftlab.ingestion.infrastructure.raw_store import DurableRawMatchStore
from riftlab.ingestion.models import (
    ImportJobModel,
    MatchModel,
    ParticipantFinalItemsModel,
    ParticipantModel,
    PurgeTombstoneModel,
)
from riftlab.ingestion.repository import SqlAlchemyIngestionRepository
from riftlab.learning.models import LearningAttemptModel
from riftlab.ml.models import (
    DatasetManifestMatchModel,
    DatasetManifestModel,
    ModelEventModel,
    ModelVersionModel,
)
from riftlab.observability import safe_exception_description
from riftlab.players.models import PlayerProfileModel
from riftlab.ranked.models import RankedObservationModel
from riftlab.reviews.models import AnalysisFeedbackModel
from riftlab.shared.db import unit_of_work
from riftlab.shared.errors import ConflictError, InvalidRequestError, NotFoundError
from riftlab.timeline.models import (
    DecisionStateModel,
    MatchFramesModel,
    MatchReconstructionModel,
    TimelineEventModel,
)

logger = logging.getLogger(__name__)

SessionFactory = async_sessionmaker[AsyncSession]
PURGED_PREFIX = "PURGED-"
LIMITATIONS = (
    "Modelos já treinados não desaprendem: os que usaram esta partida saem de ativo e só "
    "voltam depois de retreino e revalidação sem ela.",
    "O critério de popularidade versionado no repositório é uma contagem agregada e não é "
    "recontado automaticamente.",
    "Corpus de pesquisa fora do aplicativo (coletas privadas) precisa ser congelado de novo "
    "pelo operador.",
)


class LeaseLostError(Exception):
    """O job foi reclamado por outro worker no meio do expurgo; a transação é desfeita."""


def match_ref(match_id: str, secret: str | None) -> str:
    """Referência irreversível do id para a lápide (HMAC com o segredo local)."""
    key = (secret or "riftlab-local-purge").encode()
    return hmac.new(key, f"riftlab-purge-v1:{match_id}".encode(), hashlib.sha256).hexdigest()


def _points(match_id: str) -> Any:
    return select(DecisionPointModel.id).where(DecisionPointModel.match_id == match_id)


def _analyses(match_id: str) -> Any:
    return select(AnalysisModel.id).where(AnalysisModel.decision_id.in_(_points(match_id)))


async def _count(session: AsyncSession, model: Any, *where: Any) -> int:
    return int(await session.scalar(select(func.count()).select_from(model).where(*where)) or 0)


async def purge_preview(session_factory: SessionFactory, match_id: str) -> dict[str, Any]:
    """O que o expurgo removeria e invalidaria, sem alterar nada."""
    async with session_factory() as session:
        if await session.get(MatchModel, match_id) is None:
            raise NotFoundError(f"match {match_id!r} não encontrado")
        counts = {
            "participants": await _count(
                session, ParticipantModel, ParticipantModel.match_id == match_id
            ),
            "timelineEvents": await _count(
                session, TimelineEventModel, TimelineEventModel.match_id == match_id
            ),
            "decisionStates": await _count(
                session, DecisionStateModel, DecisionStateModel.match_id == match_id
            ),
            "decisionPoints": await _count(
                session, DecisionPointModel, DecisionPointModel.match_id == match_id
            ),
            "analyses": await _count(
                session, AnalysisModel, AnalysisModel.decision_id.in_(_points(match_id))
            ),
            "learningAttempts": await _count(
                session,
                LearningAttemptModel,
                LearningAttemptModel.decision_id.in_(_points(match_id)),
            ),
            "feedback": await _count(
                session,
                AnalysisFeedbackModel,
                AnalysisFeedbackModel.analysis_id.in_(_analyses(match_id)),
            ),
        }
        manifests = (
            await session.scalars(
                select(DatasetManifestModel).where(
                    DatasetManifestModel.id.in_(
                        select(DatasetManifestMatchModel.manifest_id).where(
                            DatasetManifestMatchModel.match_id == match_id
                        )
                    )
                )
            )
        ).all()
        models = (
            await session.scalars(
                select(ModelVersionModel).where(
                    ModelVersionModel.dataset_manifest_id.in_([m.id for m in manifests])
                )
            )
        ).all()
        unverifiable = await _count(
            session, DatasetManifestModel, DatasetManifestModel.match_list_known.is_(False)
        )
    return {
        "matchId": match_id,
        "counts": counts,
        "datasets": [
            {
                "task": m.task,
                "patch": m.patch,
                "description": m.description,
                "alreadyInvalidated": m.invalidated_at is not None,
            }
            for m in manifests
        ],
        "models": [{"name": v.name, "kind": v.kind, "status": v.status} for v in models],
        "unverifiableDatasets": unverifiable,
        "limitations": list(LIMITATIONS),
    }


async def request_purge(
    session_factory: SessionFactory, match_id: str, confirm: str, secret: str | None
) -> ImportJob:
    """Enfileira o expurgo (idempotente). Depois de concluído, devolve o mesmo job pela lápide."""
    if confirm != match_id:
        raise InvalidRequestError("a confirmação precisa repetir o id da partida")
    async with unit_of_work(session_factory) as session:
        repo = SqlAlchemyIngestionRepository(session)
        if await repo.get_match(match_id) is None:
            tombstone = await session.scalar(
                select(PurgeTombstoneModel).where(
                    PurgeTombstoneModel.match_ref == match_ref(match_id, secret)
                )
            )
            if tombstone is not None and tombstone.job_id is not None:
                done = await repo.get_import_job(tombstone.job_id)
                if done is not None:
                    return done
            raise NotFoundError(f"match {match_id!r} não encontrado")
        active = await _active_purge(session, match_id)
        if active is not None:
            return active
        mode = await repo.get_match_import_mode(match_id) or (
            ImportMode.RIOT if match_id.startswith("BR1_") else ImportMode.DEMO
        )
        job = ImportJob(
            id=uuid.uuid4(),
            requested_match_id=match_id,
            mode=mode,
            state=ImportJobState.QUEUED,
            kind=JobKind.PURGE,
        )
        try:
            async with session.begin_nested():
                await repo.create_import_job(job)
        except ConflictError:
            winner = await _active_purge(session, match_id)
            if winner is None:  # pragma: no cover - defensivo
                raise
            return winner
        return job


async def _active_purge(session: AsyncSession, match_id: str) -> ImportJob | None:
    row = await session.scalar(
        select(ImportJobModel).where(
            ImportJobModel.requested_match_id == match_id,
            ImportJobModel.kind == JobKind.PURGE.value,
            ImportJobModel.state.in_([s.value for s in ACTIVE_IMPORT_JOB_STATES]),
        )
    )
    if row is None:
        return None
    return await SqlAlchemyIngestionRepository(session).get_import_job(row.id)


async def _delete_rows(session: AsyncSession, match_id: str) -> dict[str, int]:
    points, analyses = _points(match_id), _analyses(match_id)
    removed: dict[str, int] = {}

    async def run(name: str, statement: Any) -> None:
        result = await session.execute(statement.execution_options(synchronize_session=False))
        removed[name] = int(result.rowcount or 0)  # type: ignore[attr-defined]

    await run(
        "feedback",
        delete(AnalysisFeedbackModel).where(AnalysisFeedbackModel.analysis_id.in_(analyses)),
    )
    await run(
        "learningAttempts",
        delete(LearningAttemptModel).where(
            or_(
                LearningAttemptModel.decision_id.in_(points),
                LearningAttemptModel.analysis_id.in_(analyses),
            )
        ),
    )
    await run("analyses", delete(AnalysisModel).where(AnalysisModel.decision_id.in_(points)))
    await run(
        "decisionPoints", delete(DecisionPointModel).where(DecisionPointModel.match_id == match_id)
    )
    await run(
        "decisionExtractions",
        delete(DecisionExtractionModel).where(DecisionExtractionModel.match_id == match_id),
    )
    await run(
        "decisionStates", delete(DecisionStateModel).where(DecisionStateModel.match_id == match_id)
    )
    await run(
        "reconstructions",
        delete(MatchReconstructionModel).where(MatchReconstructionModel.match_id == match_id),
    )
    await run(
        "timelineEvents", delete(TimelineEventModel).where(TimelineEventModel.match_id == match_id)
    )
    await run("frames", delete(MatchFramesModel).where(MatchFramesModel.match_id == match_id))
    own_jobs = select(ImportJobModel.id).where(
        ImportJobModel.requested_match_id == match_id,
        ImportJobModel.kind.in_([JobKind.IMPORT.value, JobKind.PREPARE.value]),
    )
    await session.execute(
        update(ImportJobModel)
        .where(ImportJobModel.retry_of_job_id.in_(own_jobs))
        .values(retry_of_job_id=None)
        .execution_options(synchronize_session=False)
    )
    await session.execute(
        update(ImportJobModel)
        .where(ImportJobModel.match_id == match_id)
        .values(match_id=None)
        .execution_options(synchronize_session=False)
    )
    await run("importJobs", delete(ImportJobModel).where(ImportJobModel.id.in_(own_jobs)))
    await run(
        "participantFinalItems",
        delete(ParticipantFinalItemsModel).where(ParticipantFinalItemsModel.match_id == match_id),
    )
    await run("participants", delete(ParticipantModel).where(ParticipantModel.match_id == match_id))
    await run("matches", delete(MatchModel).where(MatchModel.match_id == match_id))
    return removed


async def _invalidate_derived(
    session: AsyncSession, match_id: str, now: datetime
) -> dict[str, int]:
    manifest_ids = list(
        (
            await session.scalars(
                select(DatasetManifestMatchModel.manifest_id).where(
                    DatasetManifestMatchModel.match_id == match_id
                )
            )
        ).all()
    )
    invalidated = 0
    retired = 0
    if manifest_ids:
        result = await session.execute(
            update(DatasetManifestModel)
            .where(
                DatasetManifestModel.id.in_(manifest_ids),
                DatasetManifestModel.invalidated_at.is_(None),
            )
            .values(invalidated_at=now, invalidation_reason="MATCH_PURGED")
            .execution_options(synchronize_session=False)
        )
        invalidated = int(result.rowcount or 0)  # type: ignore[attr-defined]
        active = (
            await session.scalars(
                select(ModelVersionModel).where(
                    ModelVersionModel.dataset_manifest_id.in_(manifest_ids),
                    ModelVersionModel.status == "ACTIVE",
                )
            )
        ).all()
        for version in active:
            version.status, version.retired_at = "RETIRED", now
            session.add(
                ModelEventModel(
                    model_version_id=version.id,
                    action="INVALIDATE",
                    detail="dataset com partida expurgada",
                )
            )
            retired += 1
        await session.execute(
            delete(DatasetManifestMatchModel).where(DatasetManifestMatchModel.match_id == match_id)
        )
    return {"datasetsInvalidated": invalidated, "modelsRetired": retired}


async def purge_match(
    session_factory: SessionFactory,
    raw_store: DurableRawMatchStore,
    match_id: str,
    *,
    mode: ImportMode,
    reason: str,
    secret: str | None,
    job: ImportJob | None = None,
) -> dict[str, int]:
    """Brutos primeiro (idempotente), depois linhas, invalidação, lápide e job numa transação."""
    raw_files = raw_store.purge(match_id)
    ref = match_ref(match_id, secret)
    now = datetime.now(UTC)
    async with session_factory.begin() as session:
        patch = await session.scalar(
            select(MatchModel.patch).where(MatchModel.match_id == match_id)
        )
        if patch is not None:
            await session.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
                {"key": "rule-health:" + patch},
            )
        removed = await _delete_rows(session, match_id)
        if patch is not None:
            from riftlab.timeline.health import invalidate_patch_health

            await invalidate_patch_health(session, patch)
        removed.update(await _invalidate_derived(session, match_id, now))
        removed["rawFiles"] = raw_files
        await session.execute(
            insert(PurgeTombstoneModel)
            .values(
                id=uuid.uuid4(),
                match_ref=ref,
                mode=mode.value,
                reason=reason,
                job_id=job.id if job else None,
                removed=removed,
            )
            .on_conflict_do_update(  # nova importação DEMO expurgada de novo: vale a última
                index_elements=["match_ref"],
                set_={
                    "job_id": job.id if job else None,
                    "reason": reason,
                    "removed": removed,
                    "purged_at": now,
                },
            )
        )
        scrubbed = PURGED_PREFIX + ref[:24]
        if job is not None:
            done = await session.execute(
                update(ImportJobModel)
                .where(
                    ImportJobModel.id == job.id,
                    ImportJobModel.lease_generation == job.lease_generation,
                    ImportJobModel.state == ImportJobState.RUNNING.value,
                )
                .values(
                    state=ImportJobState.SUCCEEDED.value,
                    requested_match_id=scrubbed,
                    match_id=None,
                    lease_until=None,
                    retry_at=None,
                    error_code=None,
                )
                .execution_options(synchronize_session=False)
            )
            if done.rowcount != 1:  # type: ignore[attr-defined]
                raise LeaseLostError("job reclamado por outro worker; expurgo desfeito")
        # outras tentativas de expurgo desta partida (ex.: FAILED antigas) também perdem o id
        await session.execute(
            update(ImportJobModel)
            .where(
                ImportJobModel.requested_match_id == match_id,
                ImportJobModel.kind == JobKind.PURGE.value,
            )
            .values(requested_match_id=scrubbed, match_id=None)
            .execution_options(synchronize_session=False)
        )
    return removed


async def _renew(session_factory: SessionFactory, job: ImportJob) -> None:
    while True:
        await asyncio.sleep(30)
        async with unit_of_work(session_factory) as session:
            await SqlAlchemyIngestionRepository(session).renew_lease(
                job.id, job.lease_generation, datetime.now(UTC)
            )


async def process_next_purge(
    session_factory: SessionFactory, raw_store: DurableRawMatchStore, settings: Settings
) -> bool:
    """Um ciclo do worker para PURGE. True se um expurgo foi processado (mesmo com falha)."""
    async with unit_of_work(session_factory) as session:
        job = await SqlAlchemyIngestionRepository(session).reserve_next_job(
            datetime.now(UTC), JobKind.PURGE
        )
    if job is None:
        return False
    heartbeat = asyncio.create_task(_renew(session_factory, job))
    try:
        await purge_match(
            session_factory,
            raw_store,
            job.requested_match_id,
            mode=job.mode,
            reason="USER_REQUEST",
            secret=settings.subject_hash_secret,
            job=job,
        )
    except LeaseLostError:
        logger.warning("expurgo %s perdeu a posse; outro worker conclui", job.id)
    except Exception as exc:
        logger.error("expurgo %s falhou detalhe=%s", job.id, safe_exception_description(exc))
        async with unit_of_work(session_factory) as session:
            repo = SqlAlchemyIngestionRepository(session)
            if job.attempts >= MAX_TRANSIENT_ATTEMPTS:
                await repo.mark_import_failed(job.id, job.lease_generation, "PURGE_FAILED")
            else:
                await repo.mark_import_retry_wait(
                    job.id,
                    job.lease_generation,
                    datetime.now(UTC) + timedelta(seconds=30 * job.attempts),
                    "PURGE_FAILED",
                )
    finally:
        heartbeat.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await heartbeat
    return True


async def is_purged(session_factory: SessionFactory, match_id: str, secret: str | None) -> bool:
    async with session_factory() as session:
        return (
            await session.scalar(
                select(PurgeTombstoneModel.id).where(
                    PurgeTombstoneModel.match_ref == match_ref(match_id, secret)
                )
            )
        ) is not None


async def retention(
    session_factory: SessionFactory,
    raw_store: DurableRawMatchStore,
    *,
    match_days: int,
    rank_days: int,
    secret: str | None,
    profile_days: int = 7,
    own_key: str | None = None,
    execute: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Partidas importadas antes do corte, observações de elo antigas e perfis de terceiros.

    Sem `execute`, só conta (dry-run). Com `execute`, expurga cada partida com motivo
    RETENTION e apaga observações de elo antigas, preservando a mais recente de cada fila e
    temporada da conta configurada (`own_key`). Perfis de terceiros consultados antes de
    `profile_days` saem inteiros: o perfil, todas as observações de elo e as execuções de
    sincronização terminadas da conta (CC-1103). Elo sem perfil segue a regra geral. Nunca
    devolve ids: só contagens.
    """
    now = now or datetime.now(UTC)
    match_cutoff, rank_cutoff = now - timedelta(days=match_days), now - timedelta(days=rank_days)
    profile_cutoff = now - timedelta(days=profile_days)
    async with session_factory() as session:
        old = (
            await session.execute(
                select(ImportJobModel.match_id, ImportJobModel.mode)
                .where(
                    ImportJobModel.kind == JobKind.IMPORT.value,
                    ImportJobModel.state == ImportJobState.SUCCEEDED.value,
                    ImportJobModel.match_id.is_not(None),
                    ImportJobModel.created_at < match_cutoff,
                )
                .distinct()
            )
        ).all()
        observations = (
            await session.execute(
                select(
                    RankedObservationModel.id,
                    RankedObservationModel.account_key,
                    RankedObservationModel.queue,
                    RankedObservationModel.season,
                    RankedObservationModel.captured_at,
                )
            )
        ).all()
        profiles = (
            await session.execute(
                select(PlayerProfileModel.account_key, PlayerProfileModel.refreshed_at)
            )
        ).all()
    # Terceiros: perfil buscado e não consultado de novo desde o corte (a conta própria fica).
    expired = {
        str(key) for key, refreshed in profiles if refreshed < profile_cutoff and key != own_key
    }
    third_rank = [oid for oid, account, *_ in observations if str(account) in expired]
    observations = [row for row in observations if str(row[1]) not in expired]
    newest: dict[tuple[str, str, str], datetime] = {}
    for _, account, queue, season, captured in observations:
        key = (str(account), str(queue), str(season))
        newest[key] = max(newest.get(key, captured), captured)
    stale_rank = [
        oid
        for oid, account, queue, season, captured in observations
        if captured < rank_cutoff and captured < newest[(str(account), str(queue), str(season))]
    ]
    plan = {
        "dryRun": not execute,
        "matchDays": match_days,
        "rankDays": rank_days,
        "matches": len(old),
        "matchesByMode": dict(
            sorted({mode: sum(1 for _, m in old if m == mode) for _, mode in old}.items())
        ),
        "rankObservations": len(stale_rank),
        "profileDays": profile_days,
        "playerProfiles": len(expired),
        "playerRankObservations": len(third_rank),
    }
    if not execute:
        return plan
    purged = 0
    for match_id, mode in old:
        await purge_match(
            session_factory,
            raw_store,
            match_id,
            mode=ImportMode(mode),
            reason="RETENTION",
            secret=secret,
        )
        purged += 1
    if stale_rank or third_rank:
        async with session_factory.begin() as session:
            await session.execute(
                delete(RankedObservationModel).where(
                    RankedObservationModel.id.in_(stale_rank + third_rank)
                )
            )
    if expired:
        async with session_factory.begin() as session:
            await session.execute(
                delete(HistorySyncRunModel).where(
                    HistorySyncRunModel.account_key.in_(expired),
                    HistorySyncRunModel.state.not_in(("QUEUED", "RUNNING")),
                )
            )
            await session.execute(
                delete(PlayerProfileModel).where(PlayerProfileModel.account_key.in_(expired))
            )
    return {
        **plan,
        "purgedMatches": purged,
        "deletedRankObservations": len(stale_rank),
        "deletedPlayerProfiles": len(expired),
        "deletedPlayerRankObservations": len(third_rank),
    }


def purge_summary_json(summary: dict[str, Any]) -> str:
    return json.dumps(summary, ensure_ascii=False, sort_keys=True)


def raw_store_for(settings: Settings) -> DurableRawMatchStore:
    return DurableRawMatchStore(Path(settings.raw_storage_dir))
