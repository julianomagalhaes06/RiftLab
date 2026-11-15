"""Bastidores: registro das chamadas à Riot no banco e rota só para administradores."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from auth_helpers import sign_in, unique_email
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, func, select

from riftlab.api.app import create_app
from riftlab.auth.application import ADMIN_ROLE, USER_ROLE, admins, set_role
from riftlab.auth.models import UserModel
from riftlab.backstage import calls as riot_calls
from riftlab.backstage.models import RiotApiCallModel
from riftlab.config import Settings
from riftlab.shared.db import unit_of_work

NOW = datetime.now(UTC)


@pytest_asyncio.fixture
async def clean_calls(session_factory) -> AsyncIterator[None]:
    async def wipe() -> None:
        async with unit_of_work(session_factory) as session:
            await session.execute(delete(RiotApiCallModel))

    await wipe()
    yield
    await wipe()


def _call(kind: str, outcome: str, status: int | None, at: datetime) -> riot_calls.RiotCall:
    return riot_calls.RiotCall(
        at=at,
        kind=kind,
        region="AMERICAS",
        outcome=outcome,
        status_code=status,
        duration_ms=120,
        attempt=1,
    )


@pytest.mark.asyncio
async def test_call_log_writes_buffered_calls_and_prunes_old_ones(session_factory, clean_calls):
    log = riot_calls.RiotCallLog(session_factory, "worker")
    log(_call("match", "HTTP_200", 200, NOW - timedelta(days=2)))
    assert await log.flush(now=NOW - timedelta(days=2)) == 1
    log(_call("timeline", "HTTP_200", 200, NOW))
    log(_call("league", "HTTP_AUTH", 401, NOW))
    # A limpeza roda no máximo uma vez por minuto; aqui já passou o intervalo.
    assert await log.flush(now=NOW) == 2
    async with session_factory() as session:
        rows = (
            await session.execute(
                select(RiotApiCallModel.kind, RiotApiCallModel.process).order_by(
                    RiotApiCallModel.kind
                )
            )
        ).all()
    assert rows == [("league", "worker"), ("timeline", "worker")]
    assert await log.flush(now=NOW) == 0


@pytest_asyncio.fixture
async def client(session_factory) -> AsyncIterator[tuple[AsyncClient, list[str]]]:
    app = create_app(Settings(auth_required=True))
    emails: list[str] = []
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1") as c:
            yield c, emails
    async with unit_of_work(session_factory) as session:
        await session.execute(delete(UserModel).where(UserModel.email.in_(emails)))


@pytest.mark.asyncio
async def test_only_an_admin_sees_the_backstage(session_factory, clean_calls, client):
    c, emails = client
    assert (await c.get("/api/v1/admin/backstage")).status_code == 401

    email = unique_email("admin")
    emails.append(email)
    await sign_in(c, session_factory, email)
    session = (await c.get("/api/v1/auth/session")).json()
    assert session["user"]["isAdmin"] is False
    # Conta comum: a rota responde como se não existisse.
    assert (await c.get("/api/v1/admin/backstage")).status_code == 404

    assert await set_role(session_factory, email.upper(), ADMIN_ROLE)
    assert email in await admins(session_factory)
    assert (await c.get("/api/v1/auth/session")).json()["user"]["isAdmin"] is True

    log = riot_calls.RiotCallLog(session_factory, "worker")
    log(_call("league", "HTTP_AUTH", 401, NOW - timedelta(minutes=1)))
    log(_call("match", "HTTP_200", 200, NOW))
    await log.flush()

    response = await c.get("/api/v1/admin/backstage")
    assert response.status_code == 200
    body = response.json()
    assert [(call["kind"], call["statusCode"]) for call in body["calls"]] == [
        ("match", 200),
        ("league", 401),
    ]
    assert body["riotKey"]["status"] == "OK"
    assert body["lastHour"]["calls"] == 2 and body["lastHour"]["authFailures"] == 1
    assert body["worker"]["lastRiotCallAt"] is not None
    assert set(body["counters"]) >= {"matches", "timelineEvents", "decisions", "users"}
    # Nada de e-mail ou Riot ID nos Bastidores.
    assert email not in response.text

    assert await set_role(session_factory, email, USER_ROLE)
    assert (await c.get("/api/v1/admin/backstage")).status_code == 404
    assert not await set_role(session_factory, unique_email("ninguem"), ADMIN_ROLE)
    with pytest.raises(ValueError):
        await set_role(session_factory, email, "ROOT")
    async with session_factory() as s:
        assert await s.scalar(select(func.count()).select_from(RiotApiCallModel)) == 2
