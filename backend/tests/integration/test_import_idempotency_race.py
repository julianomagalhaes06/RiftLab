"""Achado 4 da revisão CC-201: janela entre a consulta do job mais recente e a
inserção de um novo job em request_import/retry_import. Usa um gancho de
teste (_after_initial_read) para publicar, com barreira real (duas
transações Postgres genuínas, não simuladas), uma tentativa concorrente que
termina SUCCEEDED exatamente nesse intervalo — sem gancho não há como forçar
esse timing de forma determinística."""

from __future__ import annotations

import uuid

from riftlab.ingestion.application import request_import, retry_import
from riftlab.ingestion.domain import ImportJob, ImportJobState, ImportMode
from riftlab.ingestion.repository import SqlAlchemyIngestionRepository
from riftlab.shared.db import unit_of_work


async def _non_failed_jobs(
    session_factory, requested_match_id: str, mode: ImportMode
) -> list[ImportJob]:
    async with unit_of_work(session_factory) as session:
        repo = SqlAlchemyIngestionRepository(session)
        latest = await repo.get_latest_import_job(requested_match_id, mode)
        active = await repo.list_active_import_jobs(requested_match_id, mode)
    jobs = list(active)
    if latest is not None and latest.state == ImportJobState.SUCCEEDED:
        jobs.append(latest)
    return jobs


async def test_request_import_does_not_duplicate_when_concurrent_job_succeeds_in_window(
    session_factory,
):
    requested_match_id = f"BR1_{uuid.uuid4().int % 10**10}"
    mode = ImportMode.DEMO

    old_failed = ImportJob(
        id=uuid.uuid4(),
        requested_match_id=requested_match_id,
        mode=mode,
        state=ImportJobState.FAILED,
        error_code="RETRY_LIMIT_EXCEEDED",
    )
    async with unit_of_work(session_factory) as session:
        repo = SqlAlchemyIngestionRepository(session)
        await repo.create_import_job(old_failed)

    concurrent_success_id = uuid.uuid4()

    async def _concurrent_job_reaches_succeeded() -> None:
        # Simula, com uma transação Postgres real e separada, uma outra
        # tentativa para a MESMA identidade que termina SUCCEEDED bem entre
        # a leitura inicial de request_import (que viu old_failed) e sua
        # inserção subsequente.
        async with unit_of_work(session_factory) as session:
            repo = SqlAlchemyIngestionRepository(session)
            await repo.create_import_job(
                ImportJob(
                    id=concurrent_success_id,
                    requested_match_id=requested_match_id,
                    mode=mode,
                    state=ImportJobState.SUCCEEDED,
                    match_id=None,
                )
            )

    result = await request_import(
        session_factory,
        requested_match_id,
        mode,
        _after_initial_read=_concurrent_job_reaches_succeeded,
        secret=None,
    )

    non_failed = await _non_failed_jobs(session_factory, requested_match_id, mode)
    assert len(non_failed) == 1, (
        "request_import criou job duplicado nao-FAILED durante a janela de "
        f"corrida: {[(j.id, j.state) for j in non_failed]}"
    )
    assert non_failed[0].id == concurrent_success_id
    assert result.id == concurrent_success_id
    assert result.state == ImportJobState.SUCCEEDED


async def test_retry_import_does_not_duplicate_when_successor_already_succeeded_in_window(
    session_factory,
):
    requested_match_id = f"BR1_{uuid.uuid4().int % 10**10}"
    mode = ImportMode.RIOT

    old_failed = ImportJob(
        id=uuid.uuid4(),
        requested_match_id=requested_match_id,
        mode=mode,
        state=ImportJobState.FAILED,
        error_code="SOURCE_UNAVAILABLE",
    )
    async with unit_of_work(session_factory) as session:
        repo = SqlAlchemyIngestionRepository(session)
        await repo.create_import_job(old_failed)

    concurrent_success_id = uuid.uuid4()

    async def _successor_reaches_succeeded() -> None:
        # Um cliente chama retry do job antigo (old_failed) sem saber que uma
        # outra tentativa concorrente para a mesma identidade ja terminou
        # SUCCEEDED nesse meio-tempo.
        async with unit_of_work(session_factory) as session:
            repo = SqlAlchemyIngestionRepository(session)
            await repo.create_import_job(
                ImportJob(
                    id=concurrent_success_id,
                    requested_match_id=requested_match_id,
                    mode=mode,
                    state=ImportJobState.SUCCEEDED,
                    match_id=None,
                    retry_of_job_id=old_failed.id,
                )
            )

    result = await retry_import(
        session_factory,
        old_failed.id,
        _after_initial_read=_successor_reaches_succeeded,
        secret=None,
    )

    non_failed = await _non_failed_jobs(session_factory, requested_match_id, mode)
    assert len(non_failed) == 1, (
        "retry_import de um FAILED antigo criou job duplicado nao-FAILED "
        f"apos um sucessor ja SUCCEEDED: {[(j.id, j.state) for j in non_failed]}"
    )
    assert non_failed[0].id == concurrent_success_id
    assert result.id == concurrent_success_id
    assert result.state == ImportJobState.SUCCEEDED
