"""Lease/posse de import_jobs (CC-201) contra PostgreSQL real: reserva via
SELECT FOR UPDATE SKIP LOCKED, token de posse (lease_generation) guardando
toda escrita final, reclamação de lease vencido e teto de tentativas
transitórias. Concorrência real entre duas transações, não sequencial
disfarçada — mesmo padrão de test_import_job_concurrency.py."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from riftlab.ingestion.domain import (
    MAX_TRANSIENT_ATTEMPTS,
    ImportJob,
    ImportJobState,
    ImportMode,
    Match,
)
from riftlab.ingestion.repository import SqlAlchemyIngestionRepository
from riftlab.shared.db import unit_of_work


def _match_id() -> str:
    return f"BR1_{uuid.uuid4().int % 10**10}"


async def _create_job(session_factory, requested_match_id: str, mode: ImportMode = ImportMode.DEMO):
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


async def _finalize_failed(session_factory, job_id: uuid.UUID, generation: int) -> None:
    """Fecha um job num estado terminal ao fim do teste — SUCCEEDED/FAILED
    nunca são elegíveis para reserve_next_job independente de qualquer
    relógio virtual usado por outro teste (diferente de RUNNING/RETRY_WAIT
    "não vencido ainda", que um salto de relógio grande o suficiente em
    outro teste pode tornar elegível de novo e contaminar aquele teste)."""
    async with unit_of_work(session_factory) as session:
        repo = SqlAlchemyIngestionRepository(session)
        await repo.mark_import_failed(job_id, generation, "TEST_CLEANUP")


async def _create_match(session_factory, match_id: str) -> None:
    """import_jobs.match_id tem FK real para matches — mark_import_succeeded
    só pode gravar um match_id que já existe de fato."""
    async with unit_of_work(session_factory) as session:
        repo = SqlAlchemyIngestionRepository(session)
        await repo.add_match(
            Match(
                match_id=match_id,
                queue=420,
                map_id=11,
                patch="16.17",
                start_at=datetime.now(UTC),
                duration_seconds=1800,
                raw_hash="0" * 64,
            )
        )


async def test_reserve_next_job_moves_queued_to_running_with_lease_and_generation(
    session_factory,
):
    job_id = await _create_job(session_factory, _match_id())
    now = datetime.now(UTC)

    async with unit_of_work(session_factory) as session:
        repo = SqlAlchemyIngestionRepository(session)
        reserved = await repo.reserve_next_job(now)

    assert reserved is not None
    assert reserved.id == job_id
    assert reserved.state == ImportJobState.RUNNING
    assert reserved.attempts == 1
    assert reserved.lease_generation == 1
    assert reserved.lease_until is not None
    assert reserved.lease_until > now

    await _finalize_failed(session_factory, reserved.id, reserved.lease_generation)


async def test_reserve_next_job_ignores_retry_wait_not_yet_due(session_factory):
    job_id = await _create_job(session_factory, _match_id())
    now = datetime.now(UTC)

    async with unit_of_work(session_factory) as session:
        repo = SqlAlchemyIngestionRepository(session)
        reserved = await repo.reserve_next_job(now)
    assert reserved is not None

    far_future_retry_at = now + timedelta(hours=1)
    async with unit_of_work(session_factory) as session:
        repo = SqlAlchemyIngestionRepository(session)
        ok = await repo.mark_import_retry_wait(
            reserved.id,
            reserved.lease_generation,
            far_future_retry_at,
            error_code="UPSTREAM_UNAVAILABLE",
        )
    assert ok is True

    # retry_at bem no futuro: esta linha especifica nunca deve ser devolvida
    # agora, mesmo que outra linha elegível de outro teste apareça no lote.
    async with unit_of_work(session_factory) as session:
        repo = SqlAlchemyIngestionRepository(session)
        for _ in range(10):
            candidate = await repo.reserve_next_job(now + timedelta(seconds=1))
            if candidate is None:
                break
            assert candidate.id != job_id, "job com retry_at futuro nao pode ser reservado agora"
            # Libera o candidato de outro teste de volta ao estado em que
            # estava, para não interferir com aquele teste — impossível
            # reconstruir com certeza, então apenas falha explicitamente se
            # isso acontecer (não deveria: cada teste finaliza seus jobs).
            raise AssertionError(
                f"job inesperado {candidate.id} elegível durante este teste; "
                "algum teste anterior deixou um job reservável"
            )

    async with unit_of_work(session_factory) as session:
        repo = SqlAlchemyIngestionRepository(session)
        job = await repo.get_import_job(job_id)
    assert job is not None
    assert job.state == ImportJobState.RETRY_WAIT
    assert job.retry_at == far_future_retry_at

    async with unit_of_work(session_factory) as session:
        repo = SqlAlchemyIngestionRepository(session)
        cleanup_reserved = await repo.reserve_next_job(far_future_retry_at)
        assert cleanup_reserved is not None and cleanup_reserved.id == job_id
        await repo.mark_import_failed(
            cleanup_reserved.id, cleanup_reserved.lease_generation, "TEST_CLEANUP"
        )


async def test_renew_lease_succeeds_with_current_generation_only(session_factory):
    job_id = await _create_job(session_factory, _match_id())
    now = datetime.now(UTC)

    async with unit_of_work(session_factory) as session:
        repo = SqlAlchemyIngestionRepository(session)
        reserved = await repo.reserve_next_job(now)
    assert reserved is not None

    later = now + timedelta(seconds=10)
    async with unit_of_work(session_factory) as session:
        repo = SqlAlchemyIngestionRepository(session)
        renewed = await repo.renew_lease(reserved.id, reserved.lease_generation, later)
    assert renewed is True

    async with unit_of_work(session_factory) as session:
        repo = SqlAlchemyIngestionRepository(session)
        job = await repo.get_import_job(job_id)
    assert job is not None
    assert job.lease_until is not None
    assert job.lease_until >= later

    # Generation errada (posse ja mudou de maos na simulacao) nunca escreve.
    async with unit_of_work(session_factory) as session:
        repo = SqlAlchemyIngestionRepository(session)
        stale_renewed = await repo.renew_lease(
            reserved.id, reserved.lease_generation - 1, later + timedelta(seconds=10)
        )
    assert stale_renewed is False

    await _finalize_failed(session_factory, reserved.id, reserved.lease_generation)


async def test_expired_lease_is_reclaimed_and_old_owner_write_is_rejected(session_factory):
    """Simula perda de processo: worker A reserva, o lease "vence" (lease_until
    no passado), worker B reclama o job (nova generation) e so entao A tenta
    finalizar com a generation antiga -> rejeitado, nunca sobrescreve o
    trabalho de B."""
    job_id = await _create_job(session_factory, _match_id())
    now = datetime.now(UTC)

    async with unit_of_work(session_factory) as session:
        repo = SqlAlchemyIngestionRepository(session)
        worker_a = await repo.reserve_next_job(now)
    assert worker_a is not None
    assert worker_a.lease_generation == 1

    # Avanca o relogio alem do lease de A sem que A tenha feito heartbeat —
    # exatamente o que reserve_next_job usa para decidir reclamar.
    past_lease = now + timedelta(seconds=200)
    async with unit_of_work(session_factory) as session:
        repo = SqlAlchemyIngestionRepository(session)
        worker_b = await repo.reserve_next_job(past_lease)
    assert worker_b is not None
    assert worker_b.id == job_id
    assert worker_b.lease_generation == 2
    assert worker_b.attempts == 2  # reclamação conta como tentativa (evita loop infinito)

    # Retorno tardio de A: tenta finalizar com a generation antiga (1).
    async with unit_of_work(session_factory) as session:
        repo = SqlAlchemyIngestionRepository(session)
        a_write_ok = await repo.mark_import_succeeded(
            worker_a.id, worker_a.lease_generation, match_id="BR1_from_stale_worker"
        )
    assert a_write_ok is False

    # B (dono atual) ainda consegue finalizar normalmente.
    winning_match_id = _match_id()
    await _create_match(session_factory, winning_match_id)
    async with unit_of_work(session_factory) as session:
        repo = SqlAlchemyIngestionRepository(session)
        b_write_ok = await repo.mark_import_succeeded(
            worker_b.id, worker_b.lease_generation, match_id=winning_match_id
        )
    assert b_write_ok is True

    async with unit_of_work(session_factory) as session:
        repo = SqlAlchemyIngestionRepository(session)
        job = await repo.get_import_job(job_id)
    assert job is not None
    assert job.state == ImportJobState.SUCCEEDED
    assert job.match_id == winning_match_id


async def test_retry_wait_releases_lease_and_reserve_picks_it_up_when_due(session_factory):
    job_id = await _create_job(session_factory, _match_id())
    now = datetime.now(UTC)

    async with unit_of_work(session_factory) as session:
        repo = SqlAlchemyIngestionRepository(session)
        reserved = await repo.reserve_next_job(now)
    assert reserved is not None

    retry_at = now + timedelta(seconds=30)
    async with unit_of_work(session_factory) as session:
        repo = SqlAlchemyIngestionRepository(session)
        ok = await repo.mark_import_retry_wait(
            reserved.id, reserved.lease_generation, retry_at, error_code="UPSTREAM_UNAVAILABLE"
        )
    assert ok is True

    # Antes de retry_at: nao elegivel.
    async with unit_of_work(session_factory) as session:
        repo = SqlAlchemyIngestionRepository(session)
        too_early = await repo.reserve_next_job(now + timedelta(seconds=1))
    assert too_early is None or too_early.id != job_id

    # No momento (ou depois) de retry_at: elegivel de novo, nova generation.
    async with unit_of_work(session_factory) as session:
        repo = SqlAlchemyIngestionRepository(session)
        picked_up = await repo.reserve_next_job(retry_at)
    assert picked_up is not None
    assert picked_up.id == job_id
    assert picked_up.state == ImportJobState.RUNNING
    assert picked_up.attempts == 2
    assert picked_up.lease_generation == 2

    await _finalize_failed(session_factory, picked_up.id, picked_up.lease_generation)


async def test_attempts_exhausted_marks_failed_instead_of_reserving_again(session_factory):
    job_id = await _create_job(session_factory, _match_id())
    now = datetime.now(UTC)

    generation = 0
    for attempt in range(1, MAX_TRANSIENT_ATTEMPTS + 1):
        async with unit_of_work(session_factory) as session:
            repo = SqlAlchemyIngestionRepository(session)
            reserved = await repo.reserve_next_job(now)
        assert reserved is not None, f"esperava reservar na tentativa {attempt}"
        assert reserved.attempts == attempt
        generation = reserved.lease_generation

        retry_at = now + timedelta(seconds=1)
        async with unit_of_work(session_factory) as session:
            repo = SqlAlchemyIngestionRepository(session)
            ok = await repo.mark_import_retry_wait(
                reserved.id, generation, retry_at, error_code="UPSTREAM_UNAVAILABLE"
            )
        assert ok is True
        now = retry_at

    # MAX_TRANSIENT_ATTEMPTS tentativas ja consumidas: a proxima reserva nao
    # devolve o job, marca FAILED com causa estavel.
    async with unit_of_work(session_factory) as session:
        repo = SqlAlchemyIngestionRepository(session)
        should_be_none = await repo.reserve_next_job(now)
    assert should_be_none is None or should_be_none.id != job_id

    async with unit_of_work(session_factory) as session:
        repo = SqlAlchemyIngestionRepository(session)
        job = await repo.get_import_job(job_id)
    assert job is not None
    assert job.state == ImportJobState.FAILED
    assert job.error_code == "RETRY_LIMIT_EXCEEDED"


async def test_get_latest_import_job_returns_most_recent_by_identity(session_factory):
    requested_match_id = _match_id()
    first_id = await _create_job(session_factory, requested_match_id)

    now = datetime.now(UTC)
    async with unit_of_work(session_factory) as session:
        repo = SqlAlchemyIngestionRepository(session)
        reserved = await repo.reserve_next_job(now)
        assert reserved is not None and reserved.id == first_id
        await repo.mark_import_failed(reserved.id, reserved.lease_generation, "RIOT_AUTH_FAILED")

    async with unit_of_work(session_factory) as session:
        repo = SqlAlchemyIngestionRepository(session)
        second = ImportJob(
            id=uuid.uuid4(),
            requested_match_id=requested_match_id,
            mode=ImportMode.DEMO,
            state=ImportJobState.QUEUED,
            retry_of_job_id=first_id,
        )
        await repo.create_import_job(second)

    async with unit_of_work(session_factory) as session:
        repo = SqlAlchemyIngestionRepository(session)
        latest = await repo.get_latest_import_job(requested_match_id, ImportMode.DEMO)
    assert latest is not None
    assert latest.id == second.id
    assert latest.retry_of_job_id == first_id

    # Finaliza o segundo job (senão fica QUEUED, reservável por outros testes
    # que também escaneiam a tabela inteira via reserve_next_job).
    cleanup_match_id = _match_id()
    await _create_match(session_factory, cleanup_match_id)
    async with unit_of_work(session_factory) as session:
        repo = SqlAlchemyIngestionRepository(session)
        reserved = await repo.reserve_next_job(datetime.now(UTC))
        assert reserved is not None and reserved.id == second.id
        await repo.mark_import_succeeded(
            reserved.id, reserved.lease_generation, match_id=cleanup_match_id
        )


async def test_two_concurrent_reservations_of_same_job_only_one_wins(session_factory):
    """Contenção real via FOR UPDATE SKIP LOCKED: duas transações tentam
    reservar simultaneamente o mesmo (único) job elegível; a segunda deve
    pular a linha travada, não bloquear indefinidamente nem reservar
    duplicado."""
    job_id = await _create_job(session_factory, _match_id())
    now = datetime.now(UTC)

    a_holding = asyncio.Event()
    b_attempted = asyncio.Event()
    results: dict[str, object] = {}

    async def worker_a() -> None:
        async with session_factory() as session:
            repo = SqlAlchemyIngestionRepository(session)
            reserved = await repo.reserve_next_job(now)
            results["a"] = reserved.id if reserved else None
            a_holding.set()
            # Mantem a transacao aberta (sem commit) ate B ja ter tentado
            # reservar e sido bloqueado/pulado pelo SKIP LOCKED.
            await asyncio.wait_for(b_attempted.wait(), timeout=5)
            await session.commit()

    async def worker_b() -> None:
        await asyncio.wait_for(a_holding.wait(), timeout=5)
        async with session_factory() as session:
            repo = SqlAlchemyIngestionRepository(session)
            reserve_task = asyncio.ensure_future(repo.reserve_next_job(now))
            await asyncio.sleep(0.3)
            b_attempted.set()
            reserved = await asyncio.wait_for(reserve_task, timeout=5)
            results["b"] = reserved.id if reserved else None
            await session.commit()

    await asyncio.wait_for(asyncio.gather(worker_a(), worker_b()), timeout=15)

    winners = [v for v in (results["a"], results["b"]) if v == job_id]
    assert len(winners) == 1

    async with unit_of_work(session_factory) as session:
        repo = SqlAlchemyIngestionRepository(session)
        job = await repo.get_import_job(job_id)
    assert job is not None
    assert job.state == ImportJobState.RUNNING
    assert job.attempts == 1
    assert job.lease_generation == 1

    await _finalize_failed(session_factory, job.id, job.lease_generation)
