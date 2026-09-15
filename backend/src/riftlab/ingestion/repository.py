from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from sqlalchemy import Update, or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from riftlab.ingestion.domain import (
    ACTIVE_IMPORT_JOB_STATES,
    LEASE_SECONDS,
    MAX_TRANSIENT_ATTEMPTS,
    ImportJob,
    ImportJobState,
    ImportMode,
    Match,
    Participant,
)
from riftlab.ingestion.models import (
    ImportJobModel,
    MatchModel,
    ParticipantModel,
    RiotRateLimitModel,
)
from riftlab.shared.errors import ConflictError

_RIOT_RATE_LIMIT_KEY = "global"


class SqlAlchemyIngestionRepository:
    """Implementa IngestionRepository. Não faz commit: a UoW chamadora decide o
    limite da transação."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_match(self, match: Match) -> None:
        self._session.add(
            MatchModel(
                match_id=match.match_id,
                queue=match.queue,
                map_id=match.map_id,
                patch=match.patch,
                start_at=match.start_at,
                duration_seconds=match.duration_seconds,
                raw_hash=match.raw_hash,
            )
        )

    async def add_participant(self, participant: Participant) -> None:
        self._session.add(
            ParticipantModel(
                match_id=participant.match_id,
                participant_id=participant.participant_id,
                champion_id=participant.champion_id,
                team=participant.team,
                role=participant.role,
                subject_hash=participant.subject_hash,
            )
        )

    async def create_import_job(self, job: ImportJob) -> None:
        self._session.add(
            ImportJobModel(
                id=job.id,
                requested_match_id=job.requested_match_id,
                match_id=job.match_id,
                mode=job.mode.value,
                state=job.state.value,
                attempts=job.attempts,
                lease_until=job.lease_until,
                lease_generation=job.lease_generation,
                retry_at=job.retry_at,
                error_code=job.error_code,
                retry_of_job_id=job.retry_of_job_id,
            )
        )
        try:
            # flush (nao commit) para que a violacao do indice unico parcial de
            # identidade ativa seja observada agora, dentro da transacao do
            # chamador, e traduzida para um erro de dominio.
            await self._session.flush()
        except IntegrityError as exc:
            raise ConflictError(
                "ja existe job ativo para "
                f"requested_match_id={job.requested_match_id!r} mode={job.mode.value}"
            ) from exc

    async def get_match(self, match_id: str) -> Match | None:
        row = await self._session.get(MatchModel, match_id)
        if row is None:
            return None
        return Match(
            match_id=row.match_id,
            queue=row.queue,
            map_id=row.map_id,
            patch=row.patch,
            start_at=row.start_at,
            duration_seconds=row.duration_seconds,
            raw_hash=row.raw_hash,
        )

    async def get_participant(self, match_id: str, participant_id: int) -> Participant | None:
        row = await self._session.get(
            ParticipantModel, {"match_id": match_id, "participant_id": participant_id}
        )
        if row is None:
            return None
        return Participant(
            match_id=row.match_id,
            participant_id=row.participant_id,
            champion_id=row.champion_id,
            team=row.team,
            role=row.role,
            subject_hash=row.subject_hash,
        )

    async def get_import_job(self, job_id: uuid.UUID) -> ImportJob | None:
        row = await self._session.get(ImportJobModel, job_id)
        if row is None:
            return None
        return _job_from_row(row)

    async def list_active_import_jobs(
        self, requested_match_id: str, mode: ImportMode
    ) -> list[ImportJob]:
        stmt = select(ImportJobModel).where(
            ImportJobModel.requested_match_id == requested_match_id,
            ImportJobModel.mode == mode.value,
            ImportJobModel.state.in_(tuple(s.value for s in ACTIVE_IMPORT_JOB_STATES)),
        )
        result = await self._session.execute(stmt)
        return [_job_from_row(row) for row in result.scalars()]

    async def get_latest_import_job(
        self, requested_match_id: str, mode: ImportMode
    ) -> ImportJob | None:
        stmt = (
            select(ImportJobModel)
            .where(
                ImportJobModel.requested_match_id == requested_match_id,
                ImportJobModel.mode == mode.value,
            )
            .order_by(ImportJobModel.created_at.desc())
            .limit(1)
        )
        result = await self._session.execute(stmt)
        row = result.scalars().first()
        return _job_from_row(row) if row is not None else None

    async def reserve_next_job(self, now: datetime) -> ImportJob | None:
        # Lote pequeno: jobs esgotados (attempts >= MAX_TRANSIENT_ATTEMPTS) sao
        # marcados FAILED nesta mesma transacao e pulados, sem devolver ao
        # chamador — evita reservar um job que so vai falhar de novo, sem
        # exigir SQL recursivo.
        stmt = (
            select(ImportJobModel)
            .where(
                or_(
                    ImportJobModel.state == ImportJobState.QUEUED.value,
                    (ImportJobModel.state == ImportJobState.RUNNING.value)
                    & (ImportJobModel.lease_until < now),
                    (ImportJobModel.state == ImportJobState.RETRY_WAIT.value)
                    & (ImportJobModel.retry_at <= now),
                )
            )
            .order_by(ImportJobModel.created_at)
            .limit(5)
            .with_for_update(skip_locked=True)
        )
        result = await self._session.execute(stmt)
        candidates = result.scalars().all()

        reserved: ImportJobModel | None = None
        for row in candidates:
            if row.attempts >= MAX_TRANSIENT_ATTEMPTS:
                row.state = ImportJobState.FAILED.value
                row.error_code = "RETRY_LIMIT_EXCEEDED"
                row.lease_until = None
                row.retry_at = None
                continue
            row.state = ImportJobState.RUNNING.value
            row.lease_until = now + timedelta(seconds=LEASE_SECONDS)
            row.lease_generation = row.lease_generation + 1
            row.attempts = row.attempts + 1
            row.retry_at = None
            row.error_code = None
            reserved = row
            break

        await self._session.flush()
        return _job_from_row(reserved) if reserved is not None else None

    async def renew_lease(self, job_id: uuid.UUID, generation: int, now: datetime) -> bool:
        stmt = self._guarded_update(job_id, generation).values(
            lease_until=now + timedelta(seconds=LEASE_SECONDS)
        )
        return await self._apply_guarded_update(stmt)

    async def mark_import_succeeded(
        self, job_id: uuid.UUID, generation: int, match_id: str
    ) -> bool:
        stmt = self._guarded_update(job_id, generation).values(
            state=ImportJobState.SUCCEEDED.value,
            match_id=match_id,
            lease_until=None,
            retry_at=None,
            error_code=None,
        )
        return await self._apply_guarded_update(stmt)

    async def mark_import_failed(self, job_id: uuid.UUID, generation: int, error_code: str) -> bool:
        stmt = self._guarded_update(job_id, generation).values(
            state=ImportJobState.FAILED.value,
            lease_until=None,
            retry_at=None,
            error_code=error_code,
        )
        return await self._apply_guarded_update(stmt)

    async def mark_import_retry_wait(
        self, job_id: uuid.UUID, generation: int, retry_at: datetime, error_code: str
    ) -> bool:
        stmt = self._guarded_update(job_id, generation).values(
            state=ImportJobState.RETRY_WAIT.value,
            lease_until=None,
            retry_at=retry_at,
            error_code=error_code,
        )
        return await self._apply_guarded_update(stmt)

    async def mark_import_deferred_by_cooldown(
        self, job_id: uuid.UUID, generation: int, retry_at: datetime
    ) -> bool:
        # attempts=attempts-1 desfaz o incremento que reserve_next_job já
        # aplicou ao reservar este job: um adiamento por cooldown
        # compartilhado nunca chegou a tentar rede, então nunca deveria
        # consumir orçamento de tentativa transitória (revisão CC-201,
        # achado real: cooldown longo o bastante esgotava MAX_TRANSIENT_
        # ATTEMPTS de um job que nunca tocou a API).
        stmt = self._guarded_update(job_id, generation).values(
            state=ImportJobState.RETRY_WAIT.value,
            lease_until=None,
            retry_at=retry_at,
            error_code="RIOT_RATE_LIMITED",
            attempts=ImportJobModel.attempts - 1,
        )
        return await self._apply_guarded_update(stmt)

    @staticmethod
    def _guarded_update(job_id: uuid.UUID, generation: int) -> Update:
        """Toda escrita final e o heartbeat passam por aqui: WHERE id + a
        generation que o worker recebeu na reserva + state=RUNNING. 0 linhas
        afetadas significa posse perdida (job já reclamado por outro worker) —
        nunca um erro a propagar, só um resultado False (CC-201)."""
        return (
            update(ImportJobModel)
            .where(
                ImportJobModel.id == job_id,
                ImportJobModel.lease_generation == generation,
                ImportJobModel.state == ImportJobState.RUNNING.value,
            )
            .execution_options(synchronize_session=False)
        )

    async def _apply_guarded_update(self, stmt: Update) -> bool:
        result = await self._session.execute(stmt)
        assert isinstance(result, CursorResult)
        return result.rowcount == 1

    async def get_riot_cooldown_until(self) -> datetime | None:
        row = await self._session.get(RiotRateLimitModel, _RIOT_RATE_LIMIT_KEY)
        return row.cooldown_until if row is not None else None

    async def extend_riot_cooldown_until(self, until: datetime) -> None:
        row = await self._session.get(RiotRateLimitModel, _RIOT_RATE_LIMIT_KEY)
        if row is None:
            self._session.add(RiotRateLimitModel(key=_RIOT_RATE_LIMIT_KEY, cooldown_until=until))
        elif until > row.cooldown_until:
            row.cooldown_until = until


def _job_from_row(row: ImportJobModel) -> ImportJob:
    return ImportJob(
        id=row.id,
        requested_match_id=row.requested_match_id,
        match_id=row.match_id,
        mode=ImportMode(row.mode),
        state=ImportJobState(row.state),
        attempts=row.attempts,
        lease_until=row.lease_until,
        lease_generation=row.lease_generation,
        retry_at=row.retry_at,
        error_code=row.error_code,
        retry_of_job_id=row.retry_of_job_id,
    )
