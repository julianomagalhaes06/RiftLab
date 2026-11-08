"""Card #13: telas da conta usam a conta Riot vinculada ao usuário da sessão."""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from auth_helpers import sign_in, unique_email
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from riftlab.api.app import create_app
from riftlab.auth.models import RiotLinkModel, UserModel
from riftlab.config import Settings
from riftlab.history_sync.models import HistorySyncRunModel as Run
from riftlab.ingestion.infrastructure.riot_account import (
    ALLOWED_PLATFORM,
    ALLOWED_REGIONAL_ROUTING,
    RiotAccountConfig,
)
from riftlab.ranked.domain import account_key
from riftlab.ranked.models import RankedObservationModel as Observation
from riftlab.shared.db import unit_of_work

SECRET = "synthetic-secret"
HEADERS = {"X-RiftLab-Request": "1"}


def _key(name: str) -> str:
    key = account_key(
        RiotAccountConfig(name, "BR1", ALLOWED_PLATFORM, ALLOWED_REGIONAL_ROUTING), SECRET
    )
    assert key is not None
    return key


@pytest_asyncio.fixture
async def env(session_factory, tmp_path) -> AsyncIterator[dict]:
    """App com login exigido, chave sintética e uma conta configurada diferente da vinculada."""
    configured = tmp_path / "account.json"
    configured.write_text(
        json.dumps(
            {
                "gameName": "Configurada",
                "tagLine": "BR1",
                "platform": "BR1",
                "regionalRouting": "AMERICAS",
            }
        )
    )
    settings = Settings(
        auth_required=True,
        riot_api_key="synthetic-key",
        subject_hash_secret=SECRET,
        ranked_account_file=str(configured),
    )
    linked_name = f"Vinc{uuid.uuid4().hex[:8]}"
    keys = [_key(linked_name), _key("Configurada")]
    app = create_app(settings)
    names: list[str] = []
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1") as c:
            yield {
                "client": c,
                "factory": session_factory,
                "names": names,
                "linked": linked_name,
                "keys": keys,
            }
    async with unit_of_work(session_factory) as session:
        await session.execute(delete(Run).where(Run.account_key.in_(keys)))
        await session.execute(delete(Observation).where(Observation.account_key.in_(keys)))
        await session.execute(delete(UserModel).where(UserModel.email.in_(names)))


async def _login(env: dict) -> uuid.UUID:
    email = unique_email()
    env["names"].append(email)
    return await sign_in(env["client"], env["factory"], email)


@pytest.mark.asyncio
async def test_user_without_link_never_sees_the_configured_account(env, session_factory):
    await _login(env)
    c = env["client"]
    ranked = (await c.get("/api/v1/ranked/me")).json()
    assert (ranked["status"], ranked["reason"]) == ("UNAVAILABLE", "ACCOUNT_NOT_LINKED")
    sync = await c.post("/api/v1/history/sync", headers=HEADERS)
    assert (sync.status_code, sync.json()["state"], sync.json()["reason"]) == (
        200,
        "UNAVAILABLE",
        "ACCOUNT_NOT_LINKED",
    )
    assert (await c.get("/api/v1/history/sync")).json()["reason"] == "ACCOUNT_NOT_LINKED"
    assert (await c.get("/api/v1/players/me")).json()["reason"] == "ACCOUNT_NOT_LINKED"
    async with session_factory() as session:
        assert (
            await session.scalars(select(Run).where(Run.account_key.in_(env["keys"])))
        ).first() is None


@pytest.mark.asyncio
async def test_linked_account_drives_rank_and_history_sync(env, session_factory):
    user_id = await _login(env)
    linked_key, configured_key = env["keys"]
    now = datetime.now(UTC)
    async with unit_of_work(session_factory) as session:
        session.add(
            RiotLinkModel(
                user_id=user_id,
                account_key=linked_key,
                subject_hash="a" * 64,
                game_name=env["linked"],
                tag_line="BR1",
                linked_at=now,
            )
        )
        for key, tier in ((linked_key, "SILVER"), (configured_key, "DIAMOND")):
            session.add(
                Observation(
                    account_key=key,
                    subject_hash="a" * 64,
                    queue="RANKED_SOLO_5x5",
                    season="2026",
                    captured_at=now,
                    state="READY",
                    tier=tier,
                    division="IV",
                    league_points=60,
                    wins=1,
                    losses=4,
                    origin="RIOT_LEAGUE_V4",
                )
            )
    c = env["client"]
    ranked = (await c.get("/api/v1/ranked/me")).json()
    assert (ranked["status"], ranked["position"]["tier"]) == ("READY", "SILVER")

    created = await c.post("/api/v1/history/sync", headers=HEADERS)
    assert (created.status_code, created.json()["state"]) == (202, "QUEUED")
    async with session_factory() as session:
        run = (await session.scalars(select(Run).where(Run.account_key == linked_key))).one()
    # A execução leva o Riot ID da conta vinculada só enquanto estiver ativa.
    assert (run.game_name, run.tag_line) == (env["linked"], "BR1")
    assert (await c.get("/api/v1/history/sync")).json()["state"] == "QUEUED"
