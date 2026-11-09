"""Nome no cadastro e login social: a volta do provedor, o `state` e a conta resultante."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import pytest_asyncio
from argon2 import PasswordHasher
from auth_helpers import HEADERS, PASSWORD, create_verified_user
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from riftlab.api.app import create_app
from riftlab.auth.mailer import OutgoingEmail
from riftlab.auth.models import AuthEmailCodeModel, AuthSocialIdentityModel, UserModel
from riftlab.config import Settings
from riftlab.shared.db import unit_of_work

BOTH = {
    "google_client_id": "id-google",
    "google_client_secret": "segredo-google",
    "discord_client_id": "id-discord",
    "discord_client_secret": "segredo-discord",
}


class FakeProvider:
    """Responde no lugar do Google e do Discord; `profile` é o que o provedor diz da pessoa."""

    def __init__(self) -> None:
        self.profile: dict = {}
        self.token_status = 200
        self.token_requests: list[dict[str, list[str]]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/token"):
            self.token_requests.append(parse_qs(request.content.decode()))
            return httpx.Response(self.token_status, json={"access_token": "token-falso"})
        assert request.headers["authorization"] == "Bearer token-falso"
        return httpx.Response(200, json=self.profile)


class FakeMailer:
    def __init__(self) -> None:
        self.sent: list[OutgoingEmail] = []

    async def send(self, email: OutgoingEmail) -> None:
        self.sent.append(email)


@pytest.fixture
def provider() -> FakeProvider:
    return FakeProvider()


@pytest_asyncio.fixture
async def emails(session_factory) -> AsyncIterator[list[str]]:
    created: list[str] = []
    yield created
    async with unit_of_work(session_factory) as session:
        await session.execute(delete(UserModel).where(UserModel.email.in_(created)))


async def _client(settings: Settings, provider: FakeProvider) -> AsyncIterator[AsyncClient]:
    app = create_app(settings, mailer=FakeMailer(), social_transport=httpx.MockTransport(provider))
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://127.0.0.1"
        ) as client:
            yield client


@pytest_asyncio.fixture
async def client(session_factory, provider) -> AsyncIterator[AsyncClient]:
    async for c in _client(Settings(auth_required=True, **BOTH), provider):
        yield c


def _email(emails: list[str]) -> str:
    email = f"social_{uuid.uuid4().hex[:10]}@example.test"
    emails.append(email)
    return email


def _google(email: str, *, subject: str | None = None, verified: bool = True) -> dict:
    return {
        "sub": subject or uuid.uuid4().hex,
        "email": email,
        "email_verified": verified,
        "name": "Jogadora Google",
    }


async def _begin(client: AsyncClient, name: str = "google", target: str | None = None) -> str:
    params = {"next": target} if target else {}
    response = await client.get(f"/api/v1/auth/social/{name}/start", params=params)
    assert response.status_code == 302, response.text
    return parse_qs(urlsplit(response.headers["location"]).query)["state"][0]


async def _finish(client: AsyncClient, state: str, name: str = "google", **extra) -> httpx.Response:
    params = {"code": "codigo", "state": state, **extra}
    return await client.get(f"/api/v1/auth/social/{name}/callback", params=params)


async def _session_user(client: AsyncClient) -> dict | None:
    return (await client.get("/api/v1/auth/session")).json()["user"]


async def _user(session_factory, email: str) -> UserModel | None:
    async with session_factory() as session:
        return await session.scalar(select(UserModel).where(UserModel.email == email))


async def _password_login(client: AsyncClient, email: str, password: str) -> int:
    response = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": password}, headers=HEADERS
    )
    return response.status_code


@pytest.mark.asyncio
async def test_inicio_leva_ao_provedor_com_state_em_cookie_lax(client) -> None:
    response = await client.get("/api/v1/auth/social/google/start")
    assert response.status_code == 302
    url = urlsplit(response.headers["location"])
    query = parse_qs(url.query)
    assert url.netloc == "accounts.google.com"
    assert query["client_id"] == ["id-google"]
    assert query["response_type"] == ["code"]
    assert query["scope"] == ["openid email profile"]
    assert query["redirect_uri"] == ["http://127.0.0.1:5173/api/v1/auth/social/google/callback"]
    cookie = response.headers["set-cookie"]
    assert cookie.startswith("riftlab_social=" + query["state"][0])
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie
    assert "Path=/api/v1/auth/social" in cookie


@pytest.mark.asyncio
async def test_sessao_lista_so_os_provedores_configurados(session_factory, provider) -> None:
    only_google = {k: v for k, v in BOTH.items() if k.startswith("google")}
    async for c in _client(Settings(auth_required=True, **only_google), provider):
        assert (await c.get("/api/v1/auth/session")).json()["socialProviders"] == ["google"]
        response = await c.get("/api/v1/auth/social/discord/start")
        assert response.status_code == 302
        assert response.headers["location"] == "/entrar?social=indisponivel"
    async for c in _client(Settings(auth_required=True), provider):
        assert (await c.get("/api/v1/auth/session")).json()["socialProviders"] == []


@pytest.mark.asyncio
async def test_primeira_entrada_cria_conta_confirmada_sem_senha(
    client, provider, session_factory, emails
) -> None:
    email = _email(emails)
    provider.profile = _google(email.upper(), subject="sub-1")
    state = await _begin(client)
    response = await _finish(client, state)

    assert response.status_code == 302
    assert response.headers["location"] == "/conta"
    assert provider.token_requests[0]["code"] == ["codigo"]
    assert provider.token_requests[0]["client_secret"] == ["segredo-google"]
    user = await _session_user(client)
    assert user is not None
    assert (user["email"], user["name"]) == (email, "Jogadora Google")

    stored = await _user(session_factory, email)
    assert stored is not None
    assert stored.password_hash is None
    assert stored.email_verified_at is not None
    async with session_factory() as session:
        identity = await session.get(AuthSocialIdentityModel, ("google", "sub-1"))
    assert identity is not None
    assert identity.user_id == stored.id
    # Sem senha local, a tentativa por senha cai na mesma recusa de sempre.
    await client.post("/api/v1/auth/logout", headers=HEADERS)
    assert await _password_login(client, email, PASSWORD) == 401


@pytest.mark.asyncio
async def test_discord_usa_o_nome_global_e_o_e_mail_confirmado(
    client, provider, session_factory, emails
) -> None:
    email = _email(emails)
    provider.profile = {
        "id": "99",
        "username": "usuaria",
        "global_name": "Jogadora Discord",
        "email": email,
        "verified": True,
    }
    state = await _begin(client, "discord")
    assert (await _finish(client, state, "discord")).headers["location"] == "/conta"
    stored = await _user(session_factory, email)
    assert stored is not None
    assert stored.display_name == "Jogadora Discord"


@pytest.mark.asyncio
@pytest.mark.parametrize("stored_state", [True, False])
async def test_state_diferente_ou_sem_cookie_nao_troca_o_codigo(
    client, provider, stored_state
) -> None:
    if stored_state:
        await _begin(client)
    response = await _finish(client, "state-de-outro-site")
    assert response.headers["location"] == "/entrar?social=falhou"
    assert provider.token_requests == []
    assert await _session_user(client) is None


@pytest.mark.asyncio
async def test_pessoa_que_desiste_volta_sem_sessao(client, provider) -> None:
    state = await _begin(client)
    response = await client.get(
        "/api/v1/auth/social/google/callback", params={"state": state, "error": "access_denied"}
    )
    assert response.headers["location"] == "/entrar?social=cancelado"
    assert provider.token_requests == []
    assert await _session_user(client) is None


@pytest.mark.asyncio
async def test_e_mail_nao_confirmado_no_provedor_nao_cria_conta(
    client, provider, session_factory, emails
) -> None:
    email = _email(emails)
    provider.profile = _google(email, verified=False)
    response = await _finish(client, await _begin(client))
    assert response.headers["location"] == "/entrar?social=email"
    assert await _user(session_factory, email) is None
    assert await _session_user(client) is None


@pytest.mark.asyncio
async def test_falha_na_troca_do_codigo_volta_sem_sessao(client, provider) -> None:
    provider.token_status = 400
    response = await _finish(client, await _begin(client))
    assert response.headers["location"] == "/entrar?social=falhou"
    assert await _session_user(client) is None


@pytest.mark.asyncio
async def test_conta_confirmada_e_ligada_e_mantem_a_senha(
    client, provider, session_factory, emails
) -> None:
    email = _email(emails)
    user_id = await create_verified_user(session_factory, email)
    provider.profile = _google(email)
    response = await _finish(client, await _begin(client, target="/historico"))

    assert response.headers["location"] == "/historico"
    user = await _session_user(client)
    assert user is not None
    assert user["id"] == str(user_id)
    await client.post("/api/v1/auth/logout", headers=HEADERS)
    assert await _password_login(client, email, PASSWORD) == 200


@pytest.mark.asyncio
async def test_cadastro_pendente_perde_a_senha_quando_o_dono_chega(
    client, provider, session_factory, emails
) -> None:
    """Quem cadastrou um e-mail alheio não pode herdar a conta que o dono confirmar."""
    email, now = _email(emails), datetime.now(UTC)
    async with unit_of_work(session_factory) as session:
        pending = UserModel(
            email=email, password_hash=PasswordHasher().hash(PASSWORD), created_at=now
        )
        session.add(pending)
        await session.flush()
        session.add(
            AuthEmailCodeModel(
                user_id=pending.id,
                purpose="VERIFY",
                code_hash="x",
                sent_at=now,
                expires_at=now + timedelta(minutes=15),
            )
        )

    provider.profile = _google(email)
    await _finish(client, await _begin(client))

    stored = await _user(session_factory, email)
    assert stored is not None
    assert stored.password_hash is None
    assert stored.email_verified_at is not None
    async with session_factory() as session:
        codes = await session.scalars(
            select(AuthEmailCodeModel).where(AuthEmailCodeModel.user_id == stored.id)
        )
        assert list(codes) == []
    await client.post("/api/v1/auth/logout", headers=HEADERS)
    assert await _password_login(client, email, PASSWORD) == 401


@pytest.mark.asyncio
async def test_identidade_vale_mesmo_com_outro_e_mail_no_provedor(
    client, provider, session_factory, emails
) -> None:
    first, second = _email(emails), _email(emails)
    provider.profile = _google(first, subject="sub-estavel")
    await _finish(client, await _begin(client))
    original = await _session_user(client)
    assert original is not None
    await client.post("/api/v1/auth/logout", headers=HEADERS)

    provider.profile = _google(second, subject="sub-estavel")
    response = await _finish(client, await _begin(client))

    assert response.headers["location"] == "/painel"
    user = await _session_user(client)
    assert user is not None
    assert (user["id"], user["email"]) == (original["id"], first)
    assert await _user(session_factory, second) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["https://outro.example/", "//outro.example/x", "/\\outro"])
async def test_destino_fora_do_site_e_ignorado(client, provider, emails, target) -> None:
    provider.profile = _google(_email(emails))
    response = await _finish(client, await _begin(client, target=target))
    assert response.headers["location"] == "/conta"


@pytest.mark.asyncio
async def test_cadastro_guarda_o_nome_e_recusa_nome_invalido(
    client, session_factory, emails
) -> None:
    email = _email(emails)
    body = {"email": email, "password": PASSWORD}
    url = "/api/v1/auth/register"

    assert (await client.post(url, json=body, headers=HEADERS)).status_code == 422
    invalid = await client.post(url, json={**body, "name": " a "}, headers=HEADERS)
    assert invalid.status_code == 422
    assert invalid.json()["code"] == "INVALID_NAME"
    assert await _user(session_factory, email) is None

    created = await client.post(url, json={**body, "name": "  Ana   Lima "}, headers=HEADERS)
    assert created.status_code == 202
    stored = await _user(session_factory, email)
    assert stored is not None
    assert stored.display_name == "Ana Lima"
