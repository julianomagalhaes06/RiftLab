"""POST/GET/retry de /api/v1/imports contra PostgreSQL real (CC-201):
idempotência de criação (reaproveita job ativo/SUCCEEDED, nunca duplica),
precondição de retry (só FAILED), matchId inválido, RIOT sem chave
configurada. app_client vem de tests/conftest.py (create_app real, sem
mock).

Todo teste que cria um job o finaliza (reserve_next_job + mark_import_failed)
ao final — senão fica QUEUED, elegível para reserve_next_job em qualquer
teste posterior que escaneie a tabela inteira (mesma disciplina de
test_import_job_lease.py, necessária porque não há isolamento transacional
por teste contra o Postgres real)."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

from riftlab.ingestion.domain import ImportJobState
from riftlab.ingestion.repository import SqlAlchemyIngestionRepository
from riftlab.shared.db import unit_of_work

_IMPORTS_PATH = "/api/v1/imports"


async def _consume_job(session_factory, job_id: uuid.UUID) -> None:
    async with unit_of_work(session_factory) as session:
        repo = SqlAlchemyIngestionRepository(session)
        reserved = await repo.reserve_next_job(datetime.now(UTC))
        assert reserved is not None and reserved.id == job_id, (
            f"esperava reservar {job_id}, veio {reserved.id if reserved else None} — "
            "algum teste anterior deixou um job reservável"
        )
        await repo.mark_import_failed(reserved.id, reserved.lease_generation, "TEST_CLEANUP")


async def test_create_import_demo_returns_202_and_queued_job(app_client, session_factory) -> None:
    response = await app_client.post(_IMPORTS_PATH, json={"matchId": "DEMO-0001", "mode": "DEMO"})

    assert response.status_code == 202
    body = response.json()
    assert set(body.keys()) == {"jobId", "status", "statusUrl"}
    assert body["status"] == "QUEUED"
    assert body["statusUrl"] == f"/api/v1/imports/{body['jobId']}"

    await _consume_job(session_factory, uuid.UUID(body["jobId"]))


async def test_repeated_create_import_reuses_active_job_not_duplicated(
    app_client, session_factory
) -> None:
    first = await app_client.post(_IMPORTS_PATH, json={"matchId": "DEMO-0002", "mode": "DEMO"})
    second = await app_client.post(_IMPORTS_PATH, json={"matchId": "DEMO-0002", "mode": "DEMO"})

    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["jobId"] == second.json()["jobId"]

    await _consume_job(session_factory, uuid.UUID(first.json()["jobId"]))


async def test_get_import_returns_persisted_status(app_client, session_factory) -> None:
    created = await app_client.post(_IMPORTS_PATH, json={"matchId": "DEMO-0003", "mode": "DEMO"})
    job_id = created.json()["jobId"]

    response = await app_client.get(f"{_IMPORTS_PATH}/{job_id}")

    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {"status", "kind", "attempts", "errorCode", "retryAt", "matchId"}
    assert body["status"] == "QUEUED"
    assert body["kind"] == "IMPORT"
    assert body["attempts"] == 0
    assert body["errorCode"] is None
    assert body["matchId"] is None

    await _consume_job(session_factory, uuid.UUID(job_id))


async def test_get_import_unknown_id_returns_404_problem_json(app_client) -> None:
    response = await app_client.get(f"{_IMPORTS_PATH}/{uuid.uuid4()}")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    assert body["code"] == "RESOURCE_NOT_FOUND"


async def test_create_import_rejects_malformed_match_id(app_client) -> None:
    response = await app_client.post(
        _IMPORTS_PATH, json={"matchId": "http://evil.example/x", "mode": "DEMO"}
    )
    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"


async def test_create_import_riot_mode_rejected_without_server_key(app_client) -> None:
    """app_client (tests/conftest.py) roda sem RIFTLAB_RIOT_API_KEY (modo
    demo do servidor) — RIOT é recusado antes de qualquer job, nunca cria
    algo que o worker descobriria mais tarde que não pode processar."""
    response = await app_client.post(
        _IMPORTS_PATH, json={"matchId": "BR1_1234567890", "mode": "RIOT"}
    )
    assert response.status_code == 422


async def test_retry_rejected_when_job_still_active(app_client, session_factory) -> None:
    created = await app_client.post(_IMPORTS_PATH, json={"matchId": "DEMO-0004", "mode": "DEMO"})
    job_id = created.json()["jobId"]

    response = await app_client.post(f"{_IMPORTS_PATH}/{job_id}/retry")

    assert response.status_code == 409
    assert response.json()["code"] == "IMPORT_ACTIVE"

    await _consume_job(session_factory, uuid.UUID(job_id))


async def test_retry_unknown_id_returns_404(app_client) -> None:
    response = await app_client.post(f"{_IMPORTS_PATH}/{uuid.uuid4()}/retry")
    assert response.status_code == 404


async def test_retry_after_failed_creates_new_linked_job(app_client, session_factory) -> None:
    created = await app_client.post(_IMPORTS_PATH, json={"matchId": "DEMO-0005", "mode": "DEMO"})
    original_job_id = uuid.UUID(created.json()["jobId"])

    # Simula o worker (ainda não implementado neste teste) levando o job a
    # FAILED, para exercitar só a precondição/vínculo do retry via HTTP.
    async with unit_of_work(session_factory) as session:
        repo = SqlAlchemyIngestionRepository(session)
        reserved = await repo.reserve_next_job(datetime.now(UTC))
        assert reserved is not None and reserved.id == original_job_id
        await repo.mark_import_failed(reserved.id, reserved.lease_generation, "RIOT_AUTH_FAILED")

    response = await app_client.post(f"{_IMPORTS_PATH}/{original_job_id}/retry")

    assert response.status_code == 202
    new_job_id = uuid.UUID(response.json()["jobId"])
    assert new_job_id != original_job_id

    async with unit_of_work(session_factory) as session:
        repo = SqlAlchemyIngestionRepository(session)
        new_job = await repo.get_import_job(new_job_id)
    assert new_job is not None
    assert new_job.retry_of_job_id == original_job_id
    assert new_job.state == ImportJobState.QUEUED

    await _consume_job(session_factory, new_job_id)


async def test_concurrent_create_import_for_new_identity_never_returns_two_jobs(
    app_client, session_factory
) -> None:
    match_id = f"DEMO-{uuid.uuid4().int % 10_000:04d}"
    responses = await asyncio.gather(
        app_client.post(_IMPORTS_PATH, json={"matchId": match_id, "mode": "DEMO"}),
        app_client.post(_IMPORTS_PATH, json={"matchId": match_id, "mode": "DEMO"}),
    )
    assert all(r.status_code == 202 for r in responses)
    job_ids = {r.json()["jobId"] for r in responses}
    assert len(job_ids) == 1

    await _consume_job(session_factory, uuid.UUID(next(iter(job_ids))))
