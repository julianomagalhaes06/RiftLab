"""Card #10: contas locais, sessão por cookie, CSRF e limite de tentativas."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select, update

from riftlab.api.app import create_app
from riftlab.auth.application import LoginThrottle
from riftlab.auth.models import AuthSessionModel, UserModel
from riftlab.config import Settings
from riftlab.shared.db import unit_of_work

HEADERS = {"X-RiftLab-Request": "1"}
PASSWORD = "senha-sintetica-123"


@pytest_asyncio.fixture
async def names(session_factory) -> AsyncIterator[list[str]]:
    """Nomes únicos por teste; as contas criadas saem no fim (a DB é compartilhada)."""
    created: list[str] = []
    yield created
    async with unit_of_work(session_factory) as session:
        await session.execute(
            delete(UserModel).where(UserModel.username_key.in_([n.casefold() for n in created]))
        )


def _name(names: list[str], prefix: str = "user") -> str:
    name = f"{prefix}_{uuid.uuid4().hex[:10]}"
    names.append(name)
    return name


async def _client(settings: Settings) -> AsyncIterator[AsyncClient]:
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://127.0.0.1"
        ) as client:
            yield client


@pytest_asyncio.fixture
async def client(session_factory) -> AsyncIterator[AsyncClient]:
    async for c in _client(Settings(auth_required=True)):
        yield c


@pytest.mark.asyncio
async def test_register_opens_a_session_with_a_protected_cookie(client, names, session_factory):
    name = _name(names)
    response = await client.post(
        "/api/v1/auth/register", json={"username": name, "password": PASSWORD}, headers=HEADERS
    )
    assert response.status_code == 201, response.text
    assert response.json() == {
        "authRequired": True,
        "user": {"id": response.json()["user"]["id"], "username": name},
    }
    cookie = response.headers["set-cookie"].lower()
    assert "riftlab_session=" in cookie and "httponly" in cookie and "samesite=strict" in cookie
    session = (await client.get("/api/v1/auth/session")).json()
    assert session["user"]["username"] == name
    async with session_factory() as db:
        user = await db.scalar(select(UserModel).where(UserModel.username_key == name.casefold()))
        stored = (await db.scalars(select(AuthSessionModel.token_hash))).all()
    assert user is not None and user.password_hash.startswith("$argon2id$")
    assert PASSWORD not in user.password_hash
    token = client.cookies.get("riftlab_session")
    assert token and token not in stored  # só o hash do token fica no banco

    again = await client.post(
        "/api/v1/auth/register",
        json={"username": name.upper(), "password": PASSWORD},
        headers=HEADERS,
    )
    assert (again.status_code, again.json()["code"]) == (409, "USERNAME_TAKEN")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("username", "password", "code"),
    [
        ("ab", PASSWORD, "INVALID_USERNAME"),
        ("com espaço", PASSWORD, "INVALID_USERNAME"),
        ("valido_nome", "curta", "INVALID_PASSWORD"),
    ],
)
async def test_register_validates_username_and_password(client, username, password, code):
    response = await client.post(
        "/api/v1/auth/register", json={"username": username, "password": password}, headers=HEADERS
    )
    assert (response.status_code, response.json()["code"]) == (422, code)


@pytest.mark.asyncio
async def test_login_logout_and_identical_refusals(client, names, session_factory):
    name = _name(names)
    await client.post(
        "/api/v1/auth/register", json={"username": name, "password": PASSWORD}, headers=HEADERS
    )
    client.cookies.clear()
    wrong = await client.post(
        "/api/v1/auth/login",
        json={"username": name, "password": "outra-senha-123"},
        headers=HEADERS,
    )
    unknown = await client.post(
        "/api/v1/auth/login",
        json={"username": f"x{name}", "password": PASSWORD},
        headers=HEADERS,
    )
    for refused in (wrong, unknown):
        assert (refused.status_code, refused.json()["code"]) == (401, "INVALID_CREDENTIALS")
        assert refused.json()["detail"] == "Usuário ou senha incorretos."
        assert "set-cookie" not in refused.headers

    ok = await client.post(
        "/api/v1/auth/login", json={"username": name.upper(), "password": PASSWORD}, headers=HEADERS
    )
    assert ok.status_code == 200 and ok.json()["user"]["username"] == name
    assert (await client.get("/api/v1/matches")).status_code != 401

    out = await client.post("/api/v1/auth/logout", headers=HEADERS)
    assert out.status_code == 204
    assert (await client.get("/api/v1/auth/session")).json()["user"] is None
    assert (await client.get("/api/v1/matches")).json()["code"] == "AUTH_REQUIRED"


@pytest.mark.asyncio
async def test_expired_session_is_refused(client, names, session_factory):
    name = _name(names)
    await client.post(
        "/api/v1/auth/register", json={"username": name, "password": PASSWORD}, headers=HEADERS
    )
    async with unit_of_work(session_factory) as session:
        user_id = await session.scalar(
            select(UserModel.id).where(UserModel.username_key == name.casefold())
        )
        await session.execute(
            update(AuthSessionModel)
            .where(AuthSessionModel.user_id == user_id)
            .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
    response = await client.get("/api/v1/matches")
    assert (response.status_code, response.json()["code"]) == (401, "AUTH_REQUIRED")


@pytest.mark.asyncio
async def test_protection_requires_session_and_csrf_header_but_not_for_health(client):
    response = await client.get("/api/v1/matches")
    assert (response.status_code, response.json()["code"]) == (401, "AUTH_REQUIRED")
    assert response.headers["content-type"].startswith("application/problem+json")
    assert (await client.get("/health/live")).status_code == 200
    assert (await client.get("/api/v1/auth/session")).json() == {"authRequired": True, "user": None}
    no_header = await client.post(
        "/api/v1/auth/login", json={"username": "qualquer", "password": PASSWORD}
    )
    assert (no_header.status_code, no_header.json()["code"]) == (403, "CSRF_HEADER_MISSING")


@pytest.mark.asyncio
async def test_too_many_failures_are_throttled(client, names):
    name = _name(names)
    for _ in range(5):
        await client.post(
            "/api/v1/auth/login",
            json={"username": name, "password": "errada-errada"},
            headers=HEADERS,
        )
    blocked = await client.post(
        "/api/v1/auth/login", json={"username": name, "password": PASSWORD}, headers=HEADERS
    )
    assert (blocked.status_code, blocked.json()["code"]) == (429, "LOGIN_RATE_LIMITED")
    assert 0 < int(blocked.headers["retry-after"]) <= 900


def test_throttle_window_slides_and_success_clears_the_user_count():
    now = [0.0]
    throttle = LoginThrottle(per_user=2, per_ip=3, window_seconds=60, clock=lambda: now[0])
    throttle.failed("Ana", "1.1.1.1")
    throttle.failed("ana", "1.1.1.1")
    with pytest.raises(Exception, match="LOGIN_RATE_LIMITED"):
        throttle.check("ANA", "9.9.9.9")
    now[0] = 61
    throttle.check("ana", "1.1.1.1")
    throttle.failed("bia", "1.1.1.1")
    throttle.failed("caio", "1.1.1.1")
    throttle.failed("davi", "1.1.1.1")
    with pytest.raises(Exception, match="LOGIN_RATE_LIMITED"):
        throttle.check("eva", "1.1.1.1")  # o mesmo IP tentando vários nomes
    throttle.succeeded("bia")
    throttle.check("bia", "2.2.2.2")


@pytest.mark.asyncio
async def test_without_auth_required_routes_stay_open(session_factory):
    async for c in _client(Settings(auth_required=False)):
        assert (await c.get("/api/v1/auth/session")).json() == {"authRequired": False, "user": None}
        assert (await c.get("/api/v1/matches")).status_code == 200


# Leitura pública: sem conta, a pessoa busca jogadores e abre partidas, como em sites de
# estatísticas. Os parâmetros de exemplo não existem no banco: o que importa é que a rota não
# responda 401 (pode responder 404, 422 ou 503 conforme a falta de dado ou de chave da Riot).
PUBLIC_REQUESTS = (
    ("GET", "/api/v1/catalog/current", None),
    ("GET", "/api/v1/champions/patterns", None),
    ("POST", "/api/v1/players/lookup", {"gameName": "Alguem", "tagLine": "BR1"}),
    ("POST", "/api/v1/players/lookup-from-match", {"matchId": "BR1_123", "participantId": 1}),
    ("POST", "/api/v1/players/live", {"gameName": "Alguem", "tagLine": "BR1"}),
    ("POST", "/api/v1/players/live/status", {"gameName": "Alguem", "tagLine": "BR1"}),
    ("GET", f"/api/v1/players/{uuid.UUID(int=1)}/sync", None),
    ("GET", f"/api/v1/matches?player={uuid.UUID(int=1)}", None),
    ("GET", "/api/v1/matches/BR1_123", None),
    ("GET", "/api/v1/matches/BR1_123/review", None),
    ("GET", "/api/v1/matches/BR1_123/timeline", None),
    ("GET", "/api/v1/matches/BR1_123/decisions", None),
    ("POST", "/api/v1/matches/BR1_123/preparation", None),
    ("GET", f"/api/v1/decisions/{uuid.UUID(int=1)}", None),
)

# Tudo o que cria ou guarda algo da pessoa, ou é da conta própria, continua exigindo login.
ACCOUNT_REQUESTS = (
    ("GET", "/api/v1/matches", None),
    ("GET", "/api/v1/matches?scope=demo", None),
    ("POST", "/api/v1/analyses", {}),
    ("GET", f"/api/v1/analyses/{uuid.UUID(int=1)}", None),
    ("GET", f"/api/v1/analyses/{uuid.UUID(int=1)}/report", None),
    ("POST", f"/api/v1/analyses/{uuid.UUID(int=1)}/feedback", {}),
    ("POST", "/api/v1/learning-attempts", {}),
    ("GET", "/api/v1/learning-attempts/progress", None),
    ("POST", "/api/v1/objectives/interpret", {"text": "sobreviver"}),
    ("GET", "/api/v1/dashboard", None),
    ("GET", "/api/v1/ranked/me", None),
    ("GET", "/api/v1/players/me", None),
    ("GET", "/api/v1/history/sync", None),
    ("POST", "/api/v1/history/sync", None),
    ("POST", "/api/v1/imports", {"matchId": "DEMO-0001", "mode": "DEMO"}),
    ("GET", f"/api/v1/imports/{uuid.UUID(int=1)}", None),
    ("GET", "/api/v1/matches/BR1_123/purge-preview", None),
    ("DELETE", "/api/v1/matches/BR1_123?confirm=BR1_123", None),
    ("GET", "/api/v1/riot-link", None),
    ("POST", "/api/v1/riot-link/challenge", None),
)


@pytest.mark.asyncio
@pytest.mark.parametrize(("method", "path", "body"), PUBLIC_REQUESTS)
async def test_public_reading_works_without_an_account(client, method, path, body):
    response = await client.request(method, path, json=body, headers=HEADERS)
    assert response.status_code != 401, response.text


@pytest.mark.asyncio
@pytest.mark.parametrize(("method", "path", "body"), ACCOUNT_REQUESTS)
async def test_saving_and_own_account_routes_require_login(client, method, path, body):
    response = await client.request(method, path, json=body, headers=HEADERS)
    assert (response.status_code, response.json()["code"]) == (401, "AUTH_REQUIRED"), path


@pytest.mark.asyncio
async def test_public_routes_still_need_the_csrf_header_to_change_anything(client):
    response = await client.post(
        "/api/v1/players/lookup", json={"gameName": "Alguem", "tagLine": "BR1"}
    )
    assert (response.status_code, response.json()["code"]) == (403, "CSRF_HEADER_MISSING")


@pytest.mark.asyncio
async def test_guest_mode_no_longer_exists(client):
    response = await client.post("/api/v1/auth/guest", headers=HEADERS)
    assert response.status_code in {404, 405}


@pytest.mark.asyncio
async def test_anonymous_visitor_has_no_own_account_when_login_is_required():
    """Sem login exigido, a instalação é de uma pessoa e vale a conta do arquivo; com login
    exigido, quem navega sem conta não herda a conta de ninguém."""
    from types import SimpleNamespace

    from riftlab.auth.request_account import own_account
    from riftlab.shared.own_account import CONFIGURED

    def anonymous(required: bool) -> SimpleNamespace:
        settings = Settings(auth_required=required)
        return SimpleNamespace(
            state=SimpleNamespace(user=None),
            app=SimpleNamespace(state=SimpleNamespace(settings=settings)),
        )

    assert await own_account(anonymous(True)) is None
    assert await own_account(anonymous(False)) is CONFIGURED
