"""Card #13: telas da conta usam a conta Riot vinculada ao usuário da sessão."""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

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
from riftlab.ingestion.models import MatchModel, ParticipantModel
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
        catalog_mappings_path=str(tmp_path / "no-mapping.yaml"),
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


@pytest.mark.asyncio
async def test_dashboard_keeps_linked_identity_after_failed_rank_refresh(env, session_factory):
    """Chave da Riot expirada: a consulta ao elo falha e grava observação sem identidade. O
    Painel do usuário vinculado segue com a identidade comprovada no vínculo, e não pede para
    vincular de novo."""
    user_id = await _login(env)
    linked_key, _ = env["keys"]
    own = "b" * 64
    match_id = f"BR1_13{uuid.uuid4().int % 10**8:08d}"
    now = datetime.now(UTC)
    async with unit_of_work(session_factory) as session:
        session.add(
            RiotLinkModel(
                user_id=user_id,
                account_key=linked_key,
                subject_hash=own,
                game_name=env["linked"],
                tag_line="BR1",
                linked_at=now - timedelta(days=5),
            )
        )
        for captured_at, state, subject in (
            (now - timedelta(days=4), "READY", own),
            (now, "FAILED", None),
        ):
            session.add(
                Observation(
                    account_key=linked_key,
                    subject_hash=subject,
                    queue="RANKED_SOLO_5x5",
                    season="2026",
                    captured_at=captured_at,
                    state=state,
                    error_code="RIOT_AUTH_FAILED" if state == "FAILED" else None,
                    origin="RIOT_LEAGUE_V4",
                )
            )
        session.add(
            MatchModel(
                match_id=match_id,
                queue=420,
                map_id=11,
                patch="16.20",
                start_at=now - timedelta(days=3),
                duration_seconds=1800,
                raw_hash="c" * 64,
            )
        )
        await session.flush()
        session.add(
            ParticipantModel(
                match_id=match_id,
                participant_id=1,
                champion_id=51,
                team=100,
                role="BOTTOM",
                subject_hash=own,
                win=True,
                kills=1,
                deaths=1,
                assists=1,
            )
        )
    try:
        body = (await env["client"].get("/api/v1/dashboard")).json()
        assert (body["status"], body["totalMatches"]) == ("READY", 1)
        assert body["recentMatches"][0]["matchId"] == match_id
    finally:
        async with unit_of_work(session_factory) as session:
            await session.execute(
                delete(ParticipantModel).where(ParticipantModel.match_id == match_id)
            )
            await session.execute(delete(MatchModel).where(MatchModel.match_id == match_id))
