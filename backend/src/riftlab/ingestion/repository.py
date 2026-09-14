from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from riftlab.ingestion.domain import (
    ACTIVE_IMPORT_JOB_STATES,
    ImportJob,
    ImportJobState,
    ImportMode,
    Match,
    Participant,
)
from riftlab.ingestion.models import ImportJobModel, MatchModel, ParticipantModel
from riftlab.shared.errors import ConflictError


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
                retry_at=job.retry_at,
                error_code=job.error_code,
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


def _job_from_row(row: ImportJobModel) -> ImportJob:
    return ImportJob(
        id=row.id,
        requested_match_id=row.requested_match_id,
        match_id=row.match_id,
        mode=ImportMode(row.mode),
        state=ImportJobState(row.state),
        attempts=row.attempts,
        lease_until=row.lease_until,
        retry_at=row.retry_at,
        error_code=row.error_code,
    )
