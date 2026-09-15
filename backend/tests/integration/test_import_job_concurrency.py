"""Concorrência real contra PostgreSQL: duas transações independentes competem
pela mesma identidade ativa de import_job. Não usa mock de locking nem inserção
sequencial disfarçada de concorrência — a segunda transação envia seu INSERT ao
banco enquanto a primeira ainda está aberta (não commitada), forçando contenção
real no índice único parcial; só então a primeira é liberada para commitar."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

import pytest

from riftlab.ingestion.domain import ImportJob, ImportJobState, ImportMode, Match
from riftlab.ingestion.repository import SqlAlchemyIngestionRepository
from riftlab.shared.db import unit_of_work
from riftlab.shared.errors import ConflictError


async def test_concurrent_active_job_creation_only_one_wins(session_factory):
    requested_match_id = f"BR1_{uuid.uuid4().int % 10**10}"

    a_inserted = asyncio.Event()
    b_attempted = asyncio.Event()
    results: dict[str, str] = {}

    async def txn_a() -> None:
        async with session_factory() as session:
            repo = SqlAlchemyIngestionRepository(session)
            await repo.create_import_job(
                ImportJob(
                    id=uuid.uuid4(),
                    requested_match_id=requested_match_id,
                    mode=ImportMode.DEMO,
                    state=ImportJobState.QUEUED,
                )
            )
            a_inserted.set()
            # Mantém a transação aberta (sem commit) até a tentativa concorrente
            # já ter sido enviada ao Postgres e estar bloqueada no lock da linha
            # ainda não commitada — contenção real, não sequencial.
            await asyncio.wait_for(b_attempted.wait(), timeout=5)
            await session.commit()
        results["a"] = "committed"

    async def txn_b() -> None:
        await asyncio.wait_for(a_inserted.wait(), timeout=5)
        async with session_factory() as session:
            repo = SqlAlchemyIngestionRepository(session)
            insert_task = asyncio.ensure_future(
                repo.create_import_job(
                    ImportJob(
                        id=uuid.uuid4(),
                        requested_match_id=requested_match_id,
                        mode=ImportMode.DEMO,
                        state=ImportJobState.QUEUED,
                    )
                )
            )
            # Dá tempo do INSERT alcançar o Postgres e bloquear no lock da linha
            # de A, ainda não commitada.
            await asyncio.sleep(0.3)
            b_attempted.set()
            with pytest.raises(ConflictError):
                await asyncio.wait_for(insert_task, timeout=5)
            # session.close() (ao sair do `async with`) descarta a transação
            # falha via rollback; nenhum commit é tentado sobre transação abortada.
        results["b"] = "conflict"

    await asyncio.wait_for(asyncio.gather(txn_a(), txn_b()), timeout=15)

    assert results == {"a": "committed", "b": "conflict"}

    async with unit_of_work(session_factory) as session:
        repo = SqlAlchemyIngestionRepository(session)
        active_jobs = await repo.list_active_import_jobs(requested_match_id, ImportMode.DEMO)
    assert len(active_jobs) == 1

    # Finaliza o job vencedor: senão fica QUEUED, elegível para reserve_next_job
    # (CC-201) em qualquer teste posterior que escaneie a tabela inteira.
    # match_id precisa existir de fato: import_jobs.match_id tem FK real.
    cleanup_match_id = f"BR1_{uuid.uuid4().int % 10**10}"
    async with unit_of_work(session_factory) as session:
        repo = SqlAlchemyIngestionRepository(session)
        await repo.add_match(
            Match(
                match_id=cleanup_match_id,
                queue=420,
                map_id=11,
                patch="16.17",
                start_at=datetime.now(UTC),
                duration_seconds=1800,
                raw_hash="0" * 64,
            )
        )
    async with unit_of_work(session_factory) as session:
        repo = SqlAlchemyIngestionRepository(session)
        reserved = await repo.reserve_next_job(datetime.now(UTC))
        assert reserved is not None and reserved.id == active_jobs[0].id
        await repo.mark_import_succeeded(
            reserved.id, reserved.lease_generation, match_id=cleanup_match_id
        )
