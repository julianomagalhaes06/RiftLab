"""process_next_job (CC-201) contra PostgreSQL real: ciclo reserva -> busca
(DemoMatchSource real, sem rede) -> valida -> persiste, ponta a ponta —
"smoke DEMO API -> worker -> PostgreSQL" pedido pelo card. Cada teste
finaliza seus próprios jobs (mesma disciplina de test_import_job_lease.py —
o teste "nada elegível globalmente" não é escrito aqui porque
test_persistence.py cria import_jobs próprios sem essa disciplina, tornando
essa asserção especificamente frágil entre arquivos; a ausência de trabalho
já é coberta no nível do repositório em test_import_job_lease.py)."""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import delete

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
from riftlab.ingestion.models import ImportJobModel, RiotRateLimitModel
from riftlab.ingestion.repository import SqlAlchemyIngestionRepository
from riftlab.shared.db import unit_of_work
from riftlab.shared.domain import SourceOrigin
from riftlab.timeline.repository import SqlAlchemyTimelineRepository

_FAKE_SUBJECT_HASH_SECRET = "segredo-local-de-teste-nao-real"  # noqa: S105
_FAKE_RIOT_API_KEY = "RGAPI-fake-test-key-not-real"  # noqa: S105


def _riot_docs(match_id: str) -> tuple[dict, dict]:
    """Documentos Match-V5/Timeline-V5 mínimos e válidos (mesmo formato
    validado em test_riot_source.py), parametrizados por match_id para não
    colidir entre testes deste arquivo."""
    match_doc = {
        "metadata": {"matchId": match_id, "participants": ["puuid-1", "puuid-2"]},
        "info": {
            "queueId": 420,
            "mapId": 11,
            "gameVersion": "16.17.123.4567",
            "gameStartTimestamp": 1_700_000_000_000,
            "gameDuration": 1800,
            "endOfGameResult": "GameComplete",
            "participants": [
                {
                    "participantId": 1,
                    "puuid": "puuid-1",
                    "championId": 89,
                    "teamId": 100,
                    "teamPosition": "UTILITY",
                },
                {
                    "participantId": 2,
                    "puuid": "puuid-2",
                    "championId": 51,
                    "teamId": 200,
                    "teamPosition": "BOTTOM",
                },
            ],
        },
    }
    timeline_doc = {
        "metadata": {"matchId": match_id},
        "info": {
            "frameInterval": 60000,
            "participants": [
                {"participantId": 1, "puuid": "puuid-1"},
                {"participantId": 2, "puuid": "puuid-2"},
            ],
            "frames": [
                {"timestamp": 0, "participantFrames": {}, "events": []},
            ],
        },
    }
    return match_doc, timeline_doc


def _riot_client_factory(handler):
    def factory() -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    return factory


def _success_handler(match_doc: dict, timeline_doc: dict):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/timeline"):
            return httpx.Response(200, content=json.dumps(timeline_doc).encode())
        return httpx.Response(200, content=json.dumps(match_doc).encode())

    return handler


async def _delete_job(session_factory, job_id: uuid.UUID) -> None:
    """Limpa diretamente (fora do caminho de negócio) um job já terminal
    (SUCCEEDED) que reusa uma identidade fixa de fixture demo entre execuções
    da suíte. SUCCEEDED é intencionalmente imutável pelas transições guardadas
    do repositório (nunca uma escrita de negócio o move dali); o índice único
    parcial agora cobre SUCCEEDED (revisão CC-201, achado 4), então deixar
    essa linha viva bloquearia permanentemente uma nova execução deste teste
    com a mesma DEMO-XXXX contra o mesmo Postgres persistente."""
    async with unit_of_work(session_factory) as session:
        await session.execute(delete(ImportJobModel).where(ImportJobModel.id == job_id))


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

    await _delete_job(session_factory, job_id)


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


async def _reset_riot_cooldown(session_factory) -> None:
    """O cooldown compartilhado (achado real da revisão CC-201) é uma linha
    única global — precisa de reset explícito entre testes deste arquivo
    para não vazar de um teste para o outro (extend_riot_cooldown_until só
    estende, nunca encurta, por design de produção)."""
    async with unit_of_work(session_factory) as session:
        await session.execute(delete(RiotRateLimitModel))


async def test_process_next_job_riot_403_marks_failed_in_pipeline(
    session_factory, tmp_path
) -> None:
    """403 -> FAILED testado no pipeline completo (reserva -> busca ->
    finaliza via process_next_job), não só no transporte/adapter isolado."""
    await _reset_riot_cooldown(session_factory)
    match_id = f"BR1_{uuid.uuid4().int % 10**10}"
    job_id = await _create_job(session_factory, match_id, ImportMode.RIOT)
    settings = Settings(
        subject_hash_secret=_FAKE_SUBJECT_HASH_SECRET, riot_api_key=_FAKE_RIOT_API_KEY
    )
    raw_store = DurableRawMatchStore(tmp_path / "raw")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403)

    processed = await process_next_job(
        session_factory, raw_store, settings, riot_client_factory=_riot_client_factory(handler)
    )
    assert processed is True

    async with unit_of_work(session_factory) as session:
        repo = SqlAlchemyIngestionRepository(session)
        job = await repo.get_import_job(job_id)
    assert job is not None
    assert job.state == ImportJobState.FAILED
    assert job.error_code == "RIOT_AUTH_FAILED"


async def test_process_next_job_riot_503_then_retry_succeeds_in_pipeline(
    session_factory, tmp_path
) -> None:
    """503 -> RETRY_WAIT -> sucesso testado no pipeline completo (não só no
    transporte). Espera real curta (backoff de attempts=1, poucos segundos —
    não o teto de 15 min): aceitável, não é o "sleep longo" que a revisão
    pede para evitar no teste do cooldown compartilhado (esse usa relógio
    real só para provar ausência de chamada, nunca espera o teto)."""
    await _reset_riot_cooldown(session_factory)
    match_id = f"BR1_{uuid.uuid4().int % 10**10}"
    job_id = await _create_job(session_factory, match_id, ImportMode.RIOT)
    settings = Settings(
        subject_hash_secret=_FAKE_SUBJECT_HASH_SECRET, riot_api_key=_FAKE_RIOT_API_KEY
    )
    raw_store = DurableRawMatchStore(tmp_path / "raw")

    def failing_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    processed = await process_next_job(
        session_factory,
        raw_store,
        settings,
        riot_client_factory=_riot_client_factory(failing_handler),
    )
    assert processed is True

    async with unit_of_work(session_factory) as session:
        repo = SqlAlchemyIngestionRepository(session)
        job = await repo.get_import_job(job_id)
    assert job is not None
    assert job.state == ImportJobState.RETRY_WAIT
    assert job.error_code == "UPSTREAM_UNAVAILABLE"
    assert job.retry_at is not None

    wait_seconds = (job.retry_at - datetime.now(UTC)).total_seconds()
    assert wait_seconds < 5  # backoff curto (attempts=1), nunca o teto de 15 min
    await asyncio.sleep(max(wait_seconds, 0.0) + 0.2)

    match_doc, timeline_doc = _riot_docs(match_id)
    success_handler = _success_handler(match_doc, timeline_doc)
    processed_again = await process_next_job(
        session_factory,
        raw_store,
        settings,
        riot_client_factory=_riot_client_factory(success_handler),
    )
    assert processed_again is True

    async with unit_of_work(session_factory) as session:
        repo = SqlAlchemyIngestionRepository(session)
        job_after_retry = await repo.get_import_job(job_id)
    assert job_after_retry is not None
    assert job_after_retry.state == ImportJobState.SUCCEEDED
    assert job_after_retry.match_id == match_id


async def test_riot_429_sets_shared_cooldown_blocking_other_riot_jobs_without_network(
    session_factory, tmp_path
) -> None:
    """Achado real da revisão CC-201 (item 3): 429 num job RIOT tem que
    pausar TODA a fila RIOT, não só aquele job. DEMO nunca é afetado (não
    passa pelo cooldown, ver process_next_job). "Relógio virtual, sem
    sleeps longos/rede real": aqui não se espera o cooldown vencer — só se
    prova que o segundo job nunca toca rede enquanto ele está em vigor."""
    await _reset_riot_cooldown(session_factory)
    match_id_a = f"BR1_{uuid.uuid4().int % 10**10}"
    match_id_b = f"BR1_{uuid.uuid4().int % 10**10}"
    job_a_id = await _create_job(session_factory, match_id_a, ImportMode.RIOT)
    job_b_id = await _create_job(session_factory, match_id_b, ImportMode.RIOT)
    settings = Settings(
        subject_hash_secret=_FAKE_SUBJECT_HASH_SECRET, riot_api_key=_FAKE_RIOT_API_KEY
    )
    raw_store = DurableRawMatchStore(tmp_path / "raw")

    calls: list[str] = []

    def rate_limited_handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url.path))
        return httpx.Response(429, headers={"Retry-After": "5"})

    processed_a = await process_next_job(
        session_factory,
        raw_store,
        settings,
        riot_client_factory=_riot_client_factory(rate_limited_handler),
    )
    assert processed_a is True
    assert len(calls) == 1  # só o fetch de match de A — nunca chega ao timeline

    async with unit_of_work(session_factory) as session:
        repo = SqlAlchemyIngestionRepository(session)
        job_a = await repo.get_import_job(job_a_id)
    assert job_a is not None
    assert job_a.state == ImportJobState.RETRY_WAIT
    assert job_a.error_code == "RIOT_RATE_LIMITED"
    assert job_a.retry_at is not None

    def network_should_never_be_called(request: httpx.Request) -> httpx.Response:
        raise AssertionError("job B nao deveria tocar rede: cooldown compartilhado em vigor")

    processed_b = await process_next_job(
        session_factory,
        raw_store,
        settings,
        riot_client_factory=_riot_client_factory(network_should_never_be_called),
    )
    assert processed_b is True
    assert len(calls) == 1  # continua só o de A; B nunca tocou rede

    async with unit_of_work(session_factory) as session:
        repo = SqlAlchemyIngestionRepository(session)
        job_b = await repo.get_import_job(job_b_id)
    assert job_b is not None
    assert job_b.state == ImportJobState.RETRY_WAIT
    assert job_b.error_code == "RIOT_RATE_LIMITED"
    assert job_b.retry_at == job_a.retry_at  # mesmo cooldown compartilhado, não isolado por job

    # Limpa sem esperar o cooldown vencer de verdade: reserva com um "agora"
    # virtual igual ao retry_at (repositório aceita `now` explícito), finaliza.
    async with unit_of_work(session_factory) as session:
        repo = SqlAlchemyIngestionRepository(session)
        reserved_a = await repo.reserve_next_job(job_a.retry_at)
        assert reserved_a is not None and reserved_a.id == job_a_id
        await repo.mark_import_failed(reserved_a.id, reserved_a.lease_generation, "TEST_CLEANUP")
    async with unit_of_work(session_factory) as session:
        repo = SqlAlchemyIngestionRepository(session)
        reserved_b = await repo.reserve_next_job(job_b.retry_at)
        assert reserved_b is not None and reserved_b.id == job_b_id
        await repo.mark_import_failed(reserved_b.id, reserved_b.lease_generation, "TEST_CLEANUP")
    await _reset_riot_cooldown(session_factory)


async def test_riot_cooldown_never_blocks_demo_jobs(session_factory, tmp_path) -> None:
    """ "DEMO não precisa parar" (pedido explícito da revisão): mesmo com um
    cooldown RIOT bem no futuro em vigor, um job DEMO processa normalmente."""
    await _reset_riot_cooldown(session_factory)
    async with unit_of_work(session_factory) as session:
        repo = SqlAlchemyIngestionRepository(session)
        await repo.extend_riot_cooldown_until(datetime.now(UTC) + timedelta(hours=1))

    job_id = await _create_job(session_factory, "DEMO-0002", ImportMode.DEMO)
    settings = Settings(subject_hash_secret=_FAKE_SUBJECT_HASH_SECRET)
    raw_store = DurableRawMatchStore(tmp_path / "raw")

    processed = await process_next_job(session_factory, raw_store, settings)
    assert processed is True

    async with unit_of_work(session_factory) as session:
        repo = SqlAlchemyIngestionRepository(session)
        job = await repo.get_import_job(job_id)
    assert job is not None
    assert job.state == ImportJobState.SUCCEEDED

    await _delete_job(session_factory, job_id)
    await _reset_riot_cooldown(session_factory)
