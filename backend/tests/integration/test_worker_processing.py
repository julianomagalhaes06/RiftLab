"""process_next_job (CC-201) contra PostgreSQL real: ciclo reserva -> busca
(DemoMatchSource real, sem rede) -> valida -> persiste, ponta a ponta —
"smoke DEMO API -> worker -> PostgreSQL" pedido pelo card. Cada teste
finaliza seus próprios jobs (mesma disciplina de test_import_job_lease.py —
o teste "nada elegível globalmente" não é escrito aqui porque
test_persistence.py cria import_jobs próprios sem essa disciplina, tornando
essa asserção especificamente frágil entre arquivos; a ausência de trabalho
já é coberta no nível do repositório em test_import_job_lease.py)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from riftlab.config import Settings
from riftlab.ingestion.application import (
    _LeaseLostDuringPersistError,
    _persist_result,
    process_next_job,
)
from riftlab.ingestion.domain import (
    ImportJob,
    ImportJobState,
    ImportMode,
    MatchParticipantSourceDTO,
    MatchSourceResult,
    TimelineEventDTO,
    TimelineFrameDTO,
)
from riftlab.ingestion.infrastructure.raw_store import DurableRawMatchStore
from riftlab.ingestion.repository import SqlAlchemyIngestionRepository
from riftlab.shared.db import unit_of_work
from riftlab.shared.domain import SourceOrigin
from riftlab.timeline.repository import SqlAlchemyTimelineRepository

_FAKE_SUBJECT_HASH_SECRET = "segredo-local-de-teste-nao-real"  # noqa: S105


async def _create_job(session_factory, requested_match_id: str, mode: ImportMode) -> uuid.UUID:
    async with unit_of_work(session_factory) as session:
        repo = SqlAlchemyIngestionRepository(session)
        job = ImportJob(
            id=uuid.uuid4(),
            requested_match_id=requested_match_id,
            mode=mode,
            state=ImportJobState.QUEUED,
        )
        await repo.create_import_job(job)
        return job.id


async def test_process_next_job_demo_end_to_end_persists_match_participants_events(
    session_factory, tmp_path
) -> None:
    job_id = await _create_job(session_factory, "DEMO-0001", ImportMode.DEMO)
    settings = Settings(subject_hash_secret=_FAKE_SUBJECT_HASH_SECRET)
    raw_store = DurableRawMatchStore(tmp_path / "raw")

    processed = await process_next_job(session_factory, raw_store, settings)
    assert processed is True

    async with unit_of_work(session_factory) as session:
        ingestion_repo = SqlAlchemyIngestionRepository(session)
        timeline_repo = SqlAlchemyTimelineRepository(session)
        job = await ingestion_repo.get_import_job(job_id)
        assert job is not None
        assert job.state == ImportJobState.SUCCEEDED
        assert job.match_id is not None

        match = await ingestion_repo.get_match(job.match_id)
        assert match is not None
        assert match.queue > 0

        events = await timeline_repo.list_events(job.match_id)
        assert len(events) >= 1
        # source_index preserva ordem original, nao e recalculado.
        assert [e.source_index for e in events] == sorted(e.source_index for e in events)


async def test_process_next_job_demo_unknown_match_id_marks_failed_permanently(
    session_factory, tmp_path
) -> None:
    job_id = await _create_job(session_factory, "DEMO-9999", ImportMode.DEMO)
    settings = Settings(subject_hash_secret=_FAKE_SUBJECT_HASH_SECRET)
    raw_store = DurableRawMatchStore(tmp_path / "raw")

    processed = await process_next_job(session_factory, raw_store, settings)
    assert processed is True

    async with unit_of_work(session_factory) as session:
        repo = SqlAlchemyIngestionRepository(session)
        job = await repo.get_import_job(job_id)
    assert job is not None
    assert job.state == ImportJobState.FAILED
    assert job.error_code == "MATCH_SOURCE_NOT_FOUND"
    assert job.match_id is None


async def test_persist_result_rolls_back_everything_when_lease_lost_meanwhile(
    session_factory,
) -> None:
    """Reproduz o "retorno tardio do dono antigo" no ponto de persistência:
    entre a reserva e a finalização, outro worker reclama o lease vencido
    (nova generation). O dono antigo tenta persistir com a generation velha
    -> _LeaseLostDuringPersistError, e nem a partida nem os participantes
    ficam gravados (rollback da transação inteira, nunca sucesso parcial)."""
    match_id = f"BR1_{uuid.uuid4().int % 10**10}"
    job_id = await _create_job(session_factory, match_id, ImportMode.RIOT)

    now = datetime.now(UTC)
    async with unit_of_work(session_factory) as session:
        repo = SqlAlchemyIngestionRepository(session)
        stale_owner = await repo.reserve_next_job(now)
    assert stale_owner is not None and stale_owner.id == job_id

    # Simula outro worker reclamando o lease vencido (nova generation).
    async with unit_of_work(session_factory) as session:
        repo = SqlAlchemyIngestionRepository(session)
        current_owner = await repo.reserve_next_job(now + timedelta(seconds=200))
    assert current_owner is not None and current_owner.id == job_id
    assert current_owner.lease_generation == stale_owner.lease_generation + 1

    result = MatchSourceResult(
        match_id=match_id,
        origin=SourceOrigin.COMPLETED_MATCH,
        schema_version="riot-match-v5",
        raw_hash="0" * 64,
        queue=420,
        map_id=11,
        patch="16.17",
        start_at=now,
        duration_seconds=1800,
        participants=(
            MatchParticipantSourceDTO(
                participant_id=1, champion_id=89, team=100, subject_hash="hash-1"
            ),
        ),
        frames=(TimelineFrameDTO(timestamp_ms=0),),
        events=(TimelineEventDTO(source_index=0, timestamp_ms=1000, type="ITEM_PURCHASED"),),
    )

    try:
        await _persist_result(session_factory, stale_owner, result)
    except _LeaseLostDuringPersistError:
        pass
    else:
        raise AssertionError("esperava _LeaseLostDuringPersistError")

    async with unit_of_work(session_factory) as session:
        repo = SqlAlchemyIngestionRepository(session)
        match = await repo.get_match(match_id)
    assert match is None, "match nao deveria ter sido persistido (rollback)"

    # Limpa: o dono atual finaliza o job de verdade, sem deixar QUEUED/RUNNING.
    async with unit_of_work(session_factory) as session:
        repo = SqlAlchemyIngestionRepository(session)
        await repo.mark_import_failed(
            current_owner.id, current_owner.lease_generation, "TEST_CLEANUP"
        )
