"""CC-901: sincronização do histórico da conta própria (transporte Riot simulado)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from pipeline_helpers import import_demo_match
from sqlalchemy import delete, select

from riftlab.config import Settings
from riftlab.history_sync import application
from riftlab.history_sync.application import process_next_history_sync, request_sync, sync_status
from riftlab.history_sync.models import HistorySyncRunModel as Run
from riftlab.ingestion.application import request_import
from riftlab.ingestion.domain import ImportMode
from riftlab.ingestion.infrastructure.riot_transport import RiotFetchBudget, RiotHttpTransport
from riftlab.ingestion.models import ImportJobModel, PurgeTombstoneModel
from riftlab.ingestion.purge import match_ref
from riftlab.shared.db import unit_of_work

PUUID = "synthetic-own-puuid"
NOW = datetime(2026, 10, 19, 12, tzinfo=UTC)
NEW, PENDING, PURGED = "BR1_9100000001", "BR1_9100000002", "BR1_9100000003"


def _settings(
    tmp_path: Path, *, name: str = "synthetic-owner", key: str | None = "k", file: str = "a.json"
) -> Settings:
    account = tmp_path / file
    account.write_text(
        json.dumps(
            {"gameName": name, "tagLine": "BR1", "platform": "BR1", "regionalRouting": "AMERICAS"}
        )
    )
    return Settings(
        ranked_account_file=str(account), subject_hash_secret="synthetic-secret", riot_api_key=key
    )


def _transport(ids: list[str], *, status: int = 200, headers: dict | None = None):
    seen: list[httpx.Request] = []

    def responder(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if (
            request.url.host == "americas.api.riotgames.com"
            and "/riot/account/" in request.url.path
        ):
            return httpx.Response(200, json={"puuid": PUUID})
        if status != 200:
            return httpx.Response(status, headers=headers or {})
        assert request.url.path.endswith(f"{PUUID}/ids")
        return httpx.Response(200, json=ids)

    client = httpx.AsyncClient(transport=httpx.MockTransport(responder))
    budget = RiotFetchBudget(0, 3, max_total_seconds=30, stop_on_429=True)
    return RiotHttpTransport("synthetic-key", budget, client=client), client, seen


@pytest.fixture
async def clean(session_factory):
    async def wipe() -> None:
        async with unit_of_work(session_factory) as session:
            await session.execute(delete(Run))
            await session.execute(
                delete(ImportJobModel).where(
                    ImportJobModel.requested_match_id.in_([NEW, PENDING, PURGED])
                )
            )
            await session.execute(
                delete(PurgeTombstoneModel).where(
                    PurgeTombstoneModel.match_ref == match_ref(PURGED, "synthetic-secret")
                )
            )

    await wipe()
    yield
    await wipe()


@pytest.mark.asyncio
async def test_unavailable_without_key_or_account(session_factory, tmp_path, clean):
    assert (await request_sync(session_factory, _settings(tmp_path, key=None)))[0] == {
        "state": "UNAVAILABLE",
        "reason": "RIOT_KEY_MISSING",
    }
    missing = Settings(ranked_account_file=str(tmp_path / "none.json"), riot_api_key="k")
    status, created = await request_sync(session_factory, missing)
    assert (status["reason"], created) == ("ACCOUNT_NOT_CONFIGURED", False)
    async with session_factory() as session:
        assert (await session.scalars(select(Run))).first() is None


@pytest.mark.asyncio
@pytest.mark.parametrize("_demo_match", ["DEMO-0001"], indirect=True)
async def test_sync_enqueues_only_new_matches_and_respects_cooldown(
    session_factory, tmp_path, clean, _demo_match
):
    settings = _settings(tmp_path)
    await import_demo_match(session_factory, tmp_path, _demo_match)
    await request_import(session_factory, PENDING, ImportMode.RIOT, secret="synthetic-secret")
    async with unit_of_work(session_factory) as session:
        session.add(
            PurgeTombstoneModel(
                match_ref=match_ref(PURGED, "synthetic-secret"),
                mode="RIOT",
                reason="USER_REQUEST",
                removed={},
            )
        )
    first, created = await request_sync(session_factory, settings, now=NOW)
    assert (first["state"], created) == ("QUEUED", True)
    again, created_again = await request_sync(session_factory, settings, now=NOW)
    assert (again["state"], created_again) == ("QUEUED", False)

    transport, client, seen = _transport([NEW, PENDING, PURGED, _demo_match])
    try:
        assert await process_next_history_sync(
            session_factory, settings, transport=transport, now=NOW
        )
    finally:
        await client.aclose()
    assert not await process_next_history_sync(session_factory, settings, now=NOW)
    listing = next(r for r in seen if r.url.path.endswith("/ids"))
    assert listing.url.params["type"] == "ranked" and "queue" not in listing.url.params
    assert int(listing.url.params["startTime"]) == int((NOW - timedelta(days=14)).timestamp())

    status = await sync_status(session_factory, settings)
    assert status["state"] == "SUCCEEDED" and status["reason"] is None
    assert (status["listed"], status["newEnqueued"], status["alreadyKnown"]) == (4, 1, 2)
    assert status["skippedPurged"] == 1
    async with session_factory() as session:
        jobs = (
            await session.scalars(
                select(ImportJobModel).where(ImportJobModel.requested_match_id == NEW)
            )
        ).all()
        stored = (await session.scalars(select(Run))).one()
    assert [(job.mode, job.state) for job in jobs] == [("RIOT", "QUEUED")]
    # A execução guarda só contagens: nenhum id, puuid ou Riot ID.
    assert PUUID not in str(vars(stored)) and NEW not in str(vars(stored))

    finished = stored.finished_at
    cooldown, created_cd = await request_sync(session_factory, settings, now=finished)
    assert (cooldown["reason"], created_cd) == ("COOLDOWN", False)
    later, created_later = await request_sync(
        session_factory, settings, now=finished + timedelta(seconds=301)
    )
    assert (later["state"], created_later) == ("QUEUED", True)

    # A próxima listagem começa uma hora antes do início da última bem-sucedida.
    transport, client, seen = _transport([])
    try:
        await process_next_history_sync(
            session_factory, settings, transport=transport, now=finished + timedelta(seconds=302)
        )
    finally:
        await client.aclose()
    listing = next(r for r in seen if r.url.path.endswith("/ids"))
    assert int(listing.url.params["startTime"]) == int((NOW - timedelta(hours=1)).timestamp())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "headers", "code", "wait"),
    [(401, {}, "RIOT_AUTH_FAILED", 3600), (429, {"Retry-After": "900"}, "RIOT_RATE_LIMITED", 900)],
)
async def test_riot_failures_finish_failed_and_hold_new_requests(
    session_factory, tmp_path, clean, status_code, headers, code, wait
):
    settings = _settings(tmp_path)
    await request_sync(session_factory, settings, now=NOW)
    transport, client, _ = _transport([NEW], status=status_code, headers=headers)
    try:
        await process_next_history_sync(session_factory, settings, transport=transport, now=NOW)
    finally:
        await client.aclose()
    status = await sync_status(session_factory, settings)
    assert (status["state"], status["reason"], status["newEnqueued"]) == ("FAILED", code, 0)
    finished = status["finishedAt"]
    assert status["nextAllowedAt"] == finished + timedelta(seconds=wait)
    held, created = await request_sync(
        session_factory, settings, now=finished + timedelta(seconds=wait - 1)
    )
    assert (held["state"], held["reason"], created) == ("FAILED", code, False)
    assert (await request_sync(session_factory, settings, now=finished + timedelta(seconds=wait)))[
        1
    ]


@pytest.mark.asyncio
async def test_auth_failure_before_process_start_only_waits_the_normal_cooldown(
    session_factory, tmp_path, clean, monkeypatch
):
    settings = _settings(tmp_path)
    await request_sync(session_factory, settings, now=NOW)
    transport, client, _ = _transport([NEW], status=401)
    try:
        await process_next_history_sync(session_factory, settings, transport=transport, now=NOW)
    finally:
        await client.aclose()
    finished = (await sync_status(session_factory, settings))["finishedAt"]
    # Processo reiniciado (ex.: chave renovada) depois da falha.
    monkeypatch.setattr(application, "PROCESS_STARTED_AT", finished + timedelta(seconds=1))
    status = await sync_status(session_factory, settings)
    assert status["nextAllowedAt"] == finished + timedelta(seconds=300)
    assert (await request_sync(session_factory, settings, now=finished + timedelta(seconds=300)))[1]


@pytest.mark.asyncio
async def test_stale_running_and_account_change_never_block_or_leak(
    session_factory, tmp_path, clean
):
    settings = _settings(tmp_path)
    await request_sync(session_factory, settings, now=NOW)
    async with unit_of_work(session_factory) as session:
        run = (await session.scalars(select(Run))).one()
        run.state, run.started_at = "RUNNING", NOW
    status, created = await request_sync(session_factory, settings, now=NOW + timedelta(minutes=11))
    assert (status["state"], created) == ("QUEUED", True)
    async with session_factory() as session:
        states = sorted((r.state, r.error_code) for r in (await session.scalars(select(Run))).all())
    assert states == [("FAILED", "SYNC_STALE"), ("QUEUED", None)]

    changed = _settings(tmp_path, name="someone-else", file="other.json")
    transport, client, seen = _transport([NEW])
    try:
        await process_next_history_sync(session_factory, changed, transport=transport, now=NOW)
    finally:
        await client.aclose()
    assert seen == []  # conta trocada: nenhuma chamada à Riot
    status = await sync_status(session_factory, settings)
    assert (status["state"], status["reason"]) == ("FAILED", "ACCOUNT_CHANGED")


THIRD = application.RiotAccountConfig(
    "Synthetic Third", "XYZ", application.ALLOWED_PLATFORM, application.ALLOWED_REGIONAL_ROUTING
)


async def _run_row(session_factory) -> tuple[str, str | None, str | None, str | None]:
    async with session_factory() as session:
        run = (await session.scalars(select(Run))).one()
        return run.state, run.error_code, run.game_name, run.tag_line


@pytest.mark.asyncio
async def test_worker_expires_an_abandoned_run_without_waiting_for_a_new_request(
    session_factory, tmp_path, clean
):
    # Worker derrubado (SIGKILL) depois da reserva: RUNNING com o Riot ID de terceiro (CC-1216).
    settings = _settings(tmp_path)
    _, created = await request_sync(session_factory, settings, now=NOW, account=THIRD)
    assert created
    async with unit_of_work(session_factory) as session:
        run = (await session.scalars(select(Run))).one()
        run.state, run.started_at = "RUNNING", NOW
    assert await _run_row(session_factory) == ("RUNNING", None, "Synthetic Third", "XYZ")

    # Ainda dentro do prazo: nada muda.
    assert not await process_next_history_sync(
        session_factory, settings, now=NOW + timedelta(minutes=9)
    )
    assert (await _run_row(session_factory))[0] == "RUNNING"

    # Nenhum pedido novo da conta: o próprio passo do worker a encerra.
    assert not await process_next_history_sync(
        session_factory, settings, now=NOW + timedelta(minutes=11)
    )
    assert await _run_row(session_factory) == ("FAILED", "SYNC_STALE", None, None)


@pytest.mark.asyncio
async def test_unexpected_error_after_the_reservation_finishes_without_the_riot_id(
    session_factory, tmp_path, clean, monkeypatch
):
    settings = _settings(tmp_path)
    await request_sync(session_factory, settings, now=NOW, account=THIRD)

    async def broken_import(*_args, **_kwargs):
        raise RuntimeError("banco caiu no meio do laço")

    monkeypatch.setattr(application, "request_import", broken_import)
    transport, client, _ = _transport([NEW])
    try:
        with pytest.raises(RuntimeError):
            await process_next_history_sync(session_factory, settings, transport=transport, now=NOW)
    finally:
        await client.aclose()
    assert await _run_row(session_factory) == ("FAILED", "SYNC_FAILED", None, None)


@pytest.mark.asyncio
async def test_routes_report_unavailable_without_a_riot_key(app_client):
    posted = await app_client.post("/api/v1/history/sync")
    assert posted.status_code == 200
    assert posted.json()["state"] == "UNAVAILABLE"
    assert posted.json()["reason"] == "RIOT_KEY_MISSING"
    current = await app_client.get("/api/v1/history/sync")
    assert current.status_code == 200 and current.json()["state"] == "UNAVAILABLE"
