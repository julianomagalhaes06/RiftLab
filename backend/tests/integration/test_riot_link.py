"""Card #12: vínculo da conta Riot pelo desafio do ícone de perfil (Riot simulada)."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

from riftlab.api.app import create_app
from riftlab.auth import application, riot_link
from riftlab.auth.models import RiotLinkChallengeModel, RiotLinkModel, UserModel
from riftlab.auth.riot_link import RiotLinkError
from riftlab.config import Settings
from riftlab.ingestion.infrastructure.riot_transport import RiotFetchBudget, RiotHttpTransport
from riftlab.shared.db import unit_of_work
from riftlab.shared.errors import PlayerLookupError

NOW = datetime(2026, 11, 4, 12, tzinfo=UTC)
SETTINGS = Settings(riot_api_key="synthetic-key", subject_hash_secret="synthetic-secret")


class FakeRiot:
    """Account-V1 devolve o Riot ID canônico; Summoner-V4, o ícone atual (mutável)."""

    def __init__(self, icon: int, *, puuid: str = "synthetic-puuid") -> None:
        self.icon, self.puuid, self.requests = icon, puuid, 0

    def transport(self) -> RiotHttpTransport:
        def responder(request: httpx.Request) -> httpx.Response:
            self.requests += 1
            if "/riot/account/" in request.url.path:
                return httpx.Response(
                    200, json={"puuid": self.puuid, "gameName": "Jogadora", "tagLine": "BR1"}
                )
            assert request.url.path.endswith(self.puuid)
            return httpx.Response(200, json={"profileIconId": self.icon, "summonerLevel": 30})

        client = httpx.AsyncClient(transport=httpx.MockTransport(responder))
        return RiotHttpTransport(
            "synthetic-key", RiotFetchBudget(0, 2, max_total_seconds=10), client=client
        )


@pytest_asyncio.fixture
async def users(session_factory) -> AsyncIterator[list[uuid.UUID]]:
    created: list[uuid.UUID] = []

    async def make() -> uuid.UUID:
        user, _ = await application.register(
            session_factory,
            f"link_{uuid.uuid4().hex[:10]}",
            "senha-sintetica-1",
            lifetime=timedelta(days=1),
        )
        created.append(user.id)
        return user.id

    make_users = [await make(), await make()]
    yield make_users
    async with unit_of_work(session_factory) as session:
        await session.execute(delete(UserModel).where(UserModel.id.in_(created)))


@pytest.mark.asyncio
async def test_icon_challenge_links_the_account_only_with_the_requested_icon(
    session_factory, users
):
    riot = FakeRiot(icon=5)
    state = await riot_link.start_challenge(
        session_factory,
        SETTINGS,
        users[0],
        " jogadora ",
        "#br1",
        transport=riot.transport(),
        now=NOW,
    )
    assert state["state"] == "PENDING"
    assert (state["gameName"], state["tagLine"]) == ("Jogadora", "BR1")
    assert state["iconId"] in range(0, 29) and state["iconId"] != 5
    assert state["expiresAt"] == NOW + timedelta(minutes=10)
    assert riot.requests == 2

    with pytest.raises(RiotLinkError, match="ICON_MISMATCH"):
        await riot_link.verify_challenge(
            session_factory, SETTINGS, users[0], transport=riot.transport(), now=NOW
        )
    assert (await riot_link.link_state(session_factory, users[0], now=NOW))["state"] == "PENDING"

    riot.icon = state["iconId"]
    linked = await riot_link.verify_challenge(
        session_factory,
        SETTINGS,
        users[0],
        transport=riot.transport(),
        now=NOW + timedelta(minutes=9),
    )
    assert (linked["state"], linked["gameName"], linked["tagLine"]) == ("LINKED", "Jogadora", "BR1")
    async with session_factory() as session:
        link = await session.get(RiotLinkModel, users[0])
        assert await session.get(RiotLinkChallengeModel, users[0]) is None
    assert link is not None and link.subject_hash and "synthetic-puuid" not in str(vars(link))
    account = await riot_link.linked_account(session_factory, users[0])
    assert account is not None and (account.game_name, account.tag_line) == ("Jogadora", "BR1")

    # A mesma conta Riot não pode ir para outro usuário.
    with pytest.raises(RiotLinkError, match="ACCOUNT_ALREADY_LINKED"):
        await riot_link.start_challenge(
            session_factory,
            SETTINGS,
            users[1],
            "Jogadora",
            "BR1",
            transport=riot.transport(),
            now=NOW,
        )

    await riot_link.unlink(session_factory, users[0])
    assert (await riot_link.link_state(session_factory, users[0]))["state"] == "NONE"


@pytest.mark.asyncio
async def test_deleting_the_user_removes_the_riot_id_with_the_link(session_factory, users):
    riot = FakeRiot(icon=1)
    state = await riot_link.start_challenge(
        session_factory, SETTINGS, users[0], "Jogadora", "BR1", transport=riot.transport(), now=NOW
    )
    riot.icon = state["iconId"]
    await riot_link.verify_challenge(
        session_factory, SETTINGS, users[0], transport=riot.transport(), now=NOW
    )
    await riot_link.start_challenge(
        session_factory, SETTINGS, users[0], "Jogadora", "BR1", transport=riot.transport(), now=NOW
    )
    async with unit_of_work(session_factory) as session:
        await session.execute(delete(UserModel).where(UserModel.id == users[0]))
    async with session_factory() as session:
        assert await session.get(RiotLinkModel, users[0]) is None
        assert await session.get(RiotLinkChallengeModel, users[0]) is None


@pytest.mark.asyncio
async def test_expired_or_missing_challenge_asks_for_a_new_icon(session_factory, users):
    riot = FakeRiot(icon=0)
    with pytest.raises(RiotLinkError, match="NO_CHALLENGE"):
        await riot_link.verify_challenge(session_factory, SETTINGS, users[0], now=NOW)
    state = await riot_link.start_challenge(
        session_factory, SETTINGS, users[0], "Jogadora", "BR1", transport=riot.transport(), now=NOW
    )
    riot.icon = state["iconId"]
    before = riot.requests
    with pytest.raises(RiotLinkError, match="CHALLENGE_EXPIRED"):
        await riot_link.verify_challenge(
            session_factory,
            SETTINGS,
            users[0],
            transport=riot.transport(),
            now=NOW + timedelta(minutes=10),
        )
    assert riot.requests == before  # expirado: nenhuma consulta à Riot
    async with session_factory() as session:
        assert await session.get(RiotLinkChallengeModel, users[0]) is None


@pytest.mark.asyncio
async def test_link_needs_the_riot_key_and_the_hmac_secret(session_factory, users):
    for settings, code in (
        (Settings(riot_api_key=None, subject_hash_secret="s"), "RIOT_KEY_MISSING"),
        (Settings(riot_api_key="k", subject_hash_secret=None), "SECRET_MISSING"),
    ):
        with pytest.raises(PlayerLookupError) as caught:
            await riot_link.start_challenge(session_factory, settings, users[0], "Jogadora", "BR1")
        assert caught.value.code == code


@pytest.mark.asyncio
async def test_routes_require_a_session_even_without_mandatory_login(session_factory):
    app = create_app(Settings(auth_required=False))
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1") as c:
            response = await c.get("/api/v1/riot-link")
            assert (response.status_code, response.json()["code"]) == (401, "AUTH_REQUIRED")
            name = f"link_{uuid.uuid4().hex[:10]}"
            await c.post(
                "/api/v1/auth/register", json={"username": name, "password": "senha-sintetica-1"}
            )
            try:
                assert (await c.get("/api/v1/riot-link")).json() == {
                    "state": "NONE",
                    "gameName": None,
                    "tagLine": None,
                    "iconId": None,
                    "expiresAt": None,
                    "linkedAt": None,
                }
                verify = await c.post("/api/v1/riot-link/verify")
                assert (verify.status_code, verify.json()["code"]) == (503, "RIOT_KEY_MISSING")
            finally:
                async with unit_of_work(session_factory) as session:
                    await session.execute(delete(UserModel).where(UserModel.username == name))
