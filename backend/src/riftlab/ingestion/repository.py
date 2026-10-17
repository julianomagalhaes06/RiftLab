from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from sqlalchemy import DateTime, String, Update, literal, or_, select, tuple_, update
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
    JobKind,
    Match,
    Participant,
)
from riftlab.ingestion.models import (
    ImportJobModel,
    MatchModel,
    ParticipantModel,
    RiotRateLimitModel,
)
from riftlab.ingestion.score import ParticipantScore
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
                win=participant.score.win,
                kills=participant.score.kills,
                deaths=participant.score.deaths,
                assists=participant.score.assists,
                total_minions_killed=participant.score.total_minions_killed,
                neutral_minions_killed=participant.score.neutral_minions_killed,
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
                kind=job.kind.value,
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
        return _match_from_row(row)

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
            score=_score_from_row(row),
        )

    async def list_participants(self, match_id: str) -> list[Participant]:
        stmt = (
            select(ParticipantModel)
            .where(ParticipantModel.match_id == match_id)
            .order_by(ParticipantModel.participant_id)
        )
        result = await self._session.execute(stmt)
        return [
            Participant(
                match_id=row.match_id,
                participant_id=row.participant_id,
                champion_id=row.champion_id,
                team=row.team,
                role=row.role,
                subject_hash=row.subject_hash,
                score=_score_from_row(row),
            )
            for row in result.scalars()
        ]

    async def list_participant_facts_for_matches(
        self, match_ids: list[str], local_subject: str | None
    ) -> dict[str, list[tuple[Participant, bool]]]:
        """Participantes em lote com a marca "é a conta local", calculada no banco. O
        `subject_hash` devolvido é sempre vazio: o hash nunca sai desta consulta."""
        if not match_ids:
            return {}
        is_local = (
            (ParticipantModel.subject_hash == local_subject)
            if local_subject is not None
            else literal(False)
        )
        rows = await self._session.execute(
            select(ParticipantModel, is_local.label("is_local"))
            .where(ParticipantModel.match_id.in_(match_ids))
            .order_by(ParticipantModel.match_id, ParticipantModel.participant_id)
        )
        facts: dict[str, list[tuple[Participant, bool]]] = {match_id: [] for match_id in match_ids}
        for row, local in rows.tuples():
            facts[row.match_id].append(
                (
                    Participant(
                        match_id=row.match_id,
                        participant_id=row.participant_id,
                        champion_id=row.champion_id,
                        team=row.team,
                        role=row.role,
                        subject_hash="",
                        score=_score_from_row(row),
                    ),
                    bool(local),
                )
            )
        return facts

    async def list_champion_ids_for_matches(self, match_ids: list[str]) -> dict[str, list[int]]:
        """Projeção pública em lote; nunca seleciona subject_hash."""
        if not match_ids:
            return {}
        rows = await self._session.execute(
            select(ParticipantModel.match_id, ParticipantModel.champion_id)
            .where(ParticipantModel.match_id.in_(match_ids))
            .order_by(ParticipantModel.match_id, ParticipantModel.participant_id)
        )
        champions: dict[str, list[int]] = {match_id: [] for match_id in match_ids}
        for match_id, champion_id in rows:
            champions[match_id].append(champion_id)
        return champions

    async def get_match_import_mode(self, match_id: str) -> ImportMode | None:
        stmt = (
            select(ImportJobModel.mode)
            .where(
                ImportJobModel.match_id == match_id,
                ImportJobModel.kind == JobKind.IMPORT.value,
                ImportJobModel.state == ImportJobState.SUCCEEDED.value,
            )
            .order_by(ImportJobModel.created_at)
            .limit(1)
        )
        result = await self._session.execute(stmt)
        mode = result.scalar_one_or_none()
        return ImportMode(mode) if mode is not None else None

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
            ImportJobModel.kind == JobKind.IMPORT.value,
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
                ImportJobModel.kind == JobKind.IMPORT.value,
            )
            .order_by(ImportJobModel.created_at.desc())
            .limit(1)
        )
        result = await self._session.execute(stmt)
        row = result.scalars().first()
        return _job_from_row(row) if row is not None else None

    async def get_latest_preparation_job(self, match_id: str) -> ImportJob | None:
        stmt = (
            select(ImportJobModel)
            .where(
                ImportJobModel.requested_match_id == match_id,
                ImportJobModel.kind == JobKind.PREPARE.value,
            )
            .order_by(ImportJobModel.created_at.desc(), ImportJobModel.id.desc())
            .limit(1)
        )
        result = await self._session.execute(stmt)
        row = result.scalars().first()
        return _job_from_row(row) if row is not None else None

    async def list_latest_preparation_jobs(self, match_ids: list[str]) -> dict[str, ImportJob]:
        """A preparação mais recente de cada partida (uma consulta, DISTINCT ON)."""
        if not match_ids:
            return {}
        stmt = (
            select(ImportJobModel)
            .where(
                ImportJobModel.requested_match_id.in_(match_ids),
                ImportJobModel.kind == JobKind.PREPARE.value,
            )
            .distinct(ImportJobModel.requested_match_id)
            .order_by(
                ImportJobModel.requested_match_id,
                ImportJobModel.created_at.desc(),
                ImportJobModel.id.desc(),
            )
        )
        result = await self._session.execute(stmt)
        return {row.requested_match_id: _job_from_row(row) for row in result.scalars()}

    async def enqueue_preparation(
        self,
        match_id: str,
        mode: ImportMode,
        *,
        retry_of_job_id: uuid.UUID | None = None,
        force: bool = False,
    ) -> ImportJob:
        """Enfileira uma preparação (PREPARE QUEUED) da partida e devolve o job
        vigente. Sem `force`, uma preparação já ATIVA ou já concluída (READY) é
        devolvida como está, nunca duplicada. Com `force` (nova preparação
        explícita, ex.: retentativa ou nova versão de reconstrução), só uma
        preparação ATIVA impede a criação. A inserção usa SAVEPOINT: se outra
        transação enfileirou no mesmo instante, o índice único parcial rejeita
        esta e a vencedora é devolvida, sem abortar a transação do chamador
        (a importação, quando chamada de dentro dela)."""
        latest = await self.get_latest_preparation_job(match_id)
        if latest is not None:
            active = latest.state in ACTIVE_IMPORT_JOB_STATES
            done = latest.state == ImportJobState.SUCCEEDED
            if active or (done and not force):
                return latest
            retry_of_job_id = retry_of_job_id or latest.id
        job = ImportJob(
            id=uuid.uuid4(),
            requested_match_id=match_id,
            match_id=match_id,
            mode=mode,
            state=ImportJobState.QUEUED,
            retry_of_job_id=retry_of_job_id,
            kind=JobKind.PREPARE,
        )
        try:
            async with self._session.begin_nested():
                await self.create_import_job(job)
        except ConflictError:
            winner = await self.get_latest_preparation_job(match_id)
            if winner is None:  # pragma: no cover - defensivo
                raise
            return winner
        return job

    async def list_matches_page(
        self, after: tuple[datetime, str] | None, limit: int
    ) -> list[Match]:
        """Partidas importadas, da mais recente para a mais antiga: ordem
        estável (start_at DESC, match_id DESC), keyset sobre essa dupla."""
        stmt = select(MatchModel)
        if after is not None:
            stmt = stmt.where(
                tuple_(MatchModel.start_at, MatchModel.match_id)
                < tuple_(literal(after[0], DateTime(timezone=True)), literal(after[1], String))
            )
        stmt = stmt.order_by(MatchModel.start_at.desc(), MatchModel.match_id.desc()).limit(limit)
        result = await self._session.execute(stmt)
        return [_match_from_row(row) for row in result.scalars()]

    async def reserve_next_job(
        self, now: datetime, kind: JobKind = JobKind.IMPORT
    ) -> ImportJob | None:
        # Lote pequeno: jobs esgotados (attempts >= MAX_TRANSIENT_ATTEMPTS) sao
        # marcados FAILED nesta mesma transacao e pulados, sem devolver ao
        # chamador — evita reservar um job que so vai falhar de novo, sem
        # exigir SQL recursivo.
        stmt = (
            select(ImportJobModel)
            .where(
                ImportJobModel.kind == kind.value,
                or_(
                    ImportJobModel.state == ImportJobState.QUEUED.value,
                    (ImportJobModel.state == ImportJobState.RUNNING.value)
                    & (ImportJobModel.lease_until < now),
                    (ImportJobModel.state == ImportJobState.RETRY_WAIT.value)
                    & (ImportJobModel.retry_at <= now),
                ),
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
        kind=JobKind(row.kind),
    )


def _match_from_row(row: MatchModel) -> Match:
    return Match(
        match_id=row.match_id,
        queue=row.queue,
        map_id=row.map_id,
        patch=row.patch,
        start_at=row.start_at,
        duration_seconds=row.duration_seconds,
        raw_hash=row.raw_hash,
    )


def _score_from_row(row: ParticipantModel) -> ParticipantScore:
    return ParticipantScore(
        win=row.win,
        kills=row.kills,
        deaths=row.deaths,
        assists=row.assists,
        total_minions_killed=row.total_minions_killed,
        neutral_minions_killed=row.neutral_minions_killed,
    )
