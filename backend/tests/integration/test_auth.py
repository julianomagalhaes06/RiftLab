"""Contas por e-mail confirmado, sessão por cookie, CSRF e limites de tentativa."""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from auth_helpers import create_verified_user
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select, update

from riftlab.api.app import create_app
from riftlab.auth import mailer as mailer_module
from riftlab.auth.application import LoginThrottle
from riftlab.auth.mailer import OutgoingEmail, SmtpMailer, deliver, render
from riftlab.auth.models import AuthEmailCodeModel, AuthSessionModel, UserModel
from riftlab.config import Settings
from riftlab.shared.db import unit_of_work

HEADERS = {"X-RiftLab-Request": "1"}
PASSWORD = "Senha-Sintetica-1"
OTHER_PASSWORD = "Outra-Senha-2"


class FakeMailer:
    def __init__(self) -> None:
        self.sent: list[OutgoingEmail] = []

    async def send(self, email: OutgoingEmail) -> None:
        self.sent.append(email)

    def last_code(self) -> str:
        code = self.sent[-1].code
        assert code is not None
        return code


@pytest_asyncio.fixture
async def emails(session_factory) -> AsyncIterator[list[str]]:
    """E-mails únicos por teste; as contas criadas saem no fim (a DB é compartilhada)."""
    created: list[str] = []
    yield created
    async with unit_of_work(session_factory) as session:
        await session.execute(delete(UserModel).where(UserModel.email.in_(created)))


def _email(emails: list[str], prefix: str = "user") -> str:
    email = f"{prefix}_{uuid.uuid4().hex[:10]}@example.test"
    emails.append(email)
    return email


async def _client(settings: Settings, mailer=None) -> AsyncIterator[AsyncClient]:
    app = create_app(settings, mailer=mailer)
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://127.0.0.1"
        ) as client:
            yield client


@pytest.fixture
def mail() -> FakeMailer:
    return FakeMailer()


@pytest_asyncio.fixture
async def client(session_factory, mail) -> AsyncIterator[AsyncClient]:
    async for c in _client(Settings(auth_required=True), mail):
        yield c


async def _post(client: AsyncClient, path: str, body: dict):
    return await client.post(f"/api/v1/auth/{path}", json=body, headers=HEADERS)


async def _register(client: AsyncClient, email: str, password: str = PASSWORD):
    return await _post(client, "register", {"email": email, "password": password})


async def _verified(client: AsyncClient, mail: FakeMailer, email: str):
    assert (await _register(client, email)).status_code == 202
    return await _post(
        client, "verify-email", {"email": email, "code": mail.last_code(), "password": PASSWORD}
    )


async def _age_codes(session_factory, email: str, **values) -> None:
    """Move o relógio dos códigos da conta (`sent_at` para reenviar, `expires_at` para vencer)."""
    async with unit_of_work(session_factory) as session:
        user_id = await session.scalar(select(UserModel.id).where(UserModel.email == email))
        await session.execute(
            update(AuthEmailCodeModel).where(AuthEmailCodeModel.user_id == user_id).values(**values)
        )


ALLOW_RESEND = {"sent_at": datetime.now(UTC) - timedelta(minutes=5)}


@pytest.mark.asyncio
async def test_register_confirms_by_code_before_any_session(client, mail, emails, session_factory):
    email = _email(emails)
    response = await _register(client, email.upper())
    assert (response.status_code, response.json()) == (202, {"status": "CODE_SENT"})
    assert "set-cookie" not in response.headers
    assert [(m.to, m.kind) for m in mail.sent] == [(email, "VERIFY")]
    code = mail.last_code()
    assert len(code) == 6 and code.isdigit()

    # Sem confirmar: a senha certa só revela que falta o e-mail; a errada não revela nada.
    wrong = await _post(client, "login", {"email": email, "password": OTHER_PASSWORD})
    assert (wrong.status_code, wrong.json()["code"]) == (401, "INVALID_CREDENTIALS")
    pending = await _post(client, "login", {"email": email, "password": PASSWORD})
    assert (pending.status_code, pending.json()["code"]) == (403, "EMAIL_NOT_VERIFIED")
    assert "set-cookie" not in pending.headers

    async with session_factory() as session:
        user = await session.scalar(select(UserModel).where(UserModel.email == email))
        assert user is not None and user.email_verified_at is None
        stored = await session.get(AuthEmailCodeModel, (user.id, "VERIFY"))
        assert stored is not None and code not in stored.code_hash
        assert stored.code_hash.startswith("$argon2id$")
        user_id = user.id

    confirmed = await _post(
        client, "verify-email", {"email": email, "code": code, "password": PASSWORD}
    )
    assert confirmed.status_code == 200
    assert confirmed.json() == {
        "authRequired": True,
        "user": {"id": str(user_id), "email": email},
    }
    cookie = confirmed.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie and "path=/" in cookie
    token = confirmed.headers["set-cookie"].split(";", 1)[0].split("=", 1)[1]
    async with session_factory() as session:
        assert await session.get(AuthEmailCodeModel, (user_id, "VERIFY")) is None
        stored_tokens = (await session.scalars(select(AuthSessionModel.token_hash))).all()
    assert token not in stored_tokens
    assert (await client.get("/api/v1/auth/session")).json()["user"]["email"] == email


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("email", "password", "code"),
    [
        ("sem-arroba", PASSWORD, "INVALID_EMAIL"),
        ("a@b", PASSWORD, "INVALID_EMAIL"),
        ("a b@example.test", PASSWORD, "INVALID_EMAIL"),
        ("ok@example.test", "Curta-1", "INVALID_PASSWORD"),  # 7 caracteres
        ("ok@example.test", "sem-maiuscula-1", "INVALID_PASSWORD"),
        ("ok@example.test", "SemSimbolo123", "INVALID_PASSWORD"),
        ("ok@example.test", "Com espaco 1", "INVALID_PASSWORD"),  # espaço não é símbolo
        ("ok@example.test", "A!" + "x" * 127, "INVALID_PASSWORD"),
    ],
)
async def test_register_validates_email_and_password(client, mail, email, password, code):
    response = await _register(client, email, password)
    assert (response.status_code, response.json()["code"]) == (422, code)
    assert mail.sent == []


@pytest.mark.asyncio
async def test_password_of_exactly_8_chars_with_upper_and_symbol_is_enough(client, mail, emails):
    assert (await _register(client, _email(emails), "Abcdef1!")).status_code == 202


@pytest.mark.asyncio
async def test_existing_account_gets_same_answer_and_only_a_notice(client, mail, emails):
    email = _email(emails)
    assert (await _verified(client, mail, email)).status_code == 200
    client.cookies.clear()
    mail.sent.clear()

    response = await _register(client, email, OTHER_PASSWORD)
    assert (response.status_code, response.json()) == (202, {"status": "CODE_SENT"})
    assert [(m.kind, m.code) for m in mail.sent] == [("ALREADY_REGISTERED", None)]
    # A senha da conta não mudou.
    assert (await _post(client, "login", {"email": email, "password": PASSWORD})).status_code == 200


@pytest.mark.asyncio
async def test_code_only_confirms_together_with_the_password_of_the_registration(
    client, mail, emails, session_factory
):
    """Pré-cadastro alheio: o dono do e-mail recebe o código, mas a senha ativada não pode ser
    a de quem cadastrou o endereço dele."""
    email = _email(emails)
    await _register(client, email, OTHER_PASSWORD)  # quem não é dono do e-mail
    await _age_codes(session_factory, email, **ALLOW_RESEND)
    await _register(client, email, PASSWORD)  # o dono
    code = mail.last_code()
    await _register(client, email, OTHER_PASSWORD)  # troca a senha de novo, dentro do intervalo
    assert len(mail.sent) == 2  # nenhum código novo saiu

    refused = await _post(
        client, "verify-email", {"email": email, "code": code, "password": PASSWORD}
    )
    assert (refused.status_code, refused.json()["code"]) == (422, "INVALID_CODE")
    assert "set-cookie" not in refused.headers


@pytest.mark.asyncio
async def test_resend_respects_the_interval_and_replaces_the_code(
    client, mail, emails, session_factory
):
    email = _email(emails)
    await _register(client, email)
    first = mail.last_code()
    assert (await _post(client, "resend-code", {"email": email})).status_code == 202
    assert len(mail.sent) == 1  # dentro do intervalo de reenvio

    await _age_codes(session_factory, email, **ALLOW_RESEND)
    assert (await _post(client, "resend-code", {"email": email})).status_code == 202
    assert len(mail.sent) == 2
    second = mail.last_code()
    if first != second:
        old = await _post(
            client, "verify-email", {"email": email, "code": first, "password": PASSWORD}
        )
        assert old.json()["code"] == "INVALID_CODE"
    ok = await _post(client, "verify-email", {"email": email, "code": second, "password": PASSWORD})
    assert ok.status_code == 200

    # Conta confirmada ou inexistente: mesma resposta, nenhum e-mail.
    mail.sent.clear()
    client.cookies.clear()
    assert (await _post(client, "resend-code", {"email": email})).status_code == 202
    missing = _email(emails)
    assert (await _post(client, "resend-code", {"email": missing})).status_code == 202
    assert mail.sent == []


@pytest.mark.asyncio
async def test_code_dies_after_five_wrong_tries(client, mail, emails):
    email = _email(emails)
    await _register(client, email)
    code = mail.last_code()
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(5):
        response = await _post(
            client, "verify-email", {"email": email, "code": wrong, "password": PASSWORD}
        )
        assert response.json()["code"] == "INVALID_CODE"
    late = await _post(client, "verify-email", {"email": email, "code": code, "password": PASSWORD})
    assert late.json()["code"] == "INVALID_CODE"


@pytest.mark.asyncio
async def test_expired_code_is_refused(client, mail, emails, session_factory):
    email = _email(emails)
    await _register(client, email)
    await _age_codes(session_factory, email, expires_at=datetime.now(UTC) - timedelta(seconds=1))
    late = await _post(
        client, "verify-email", {"email": email, "code": mail.last_code(), "password": PASSWORD}
    )
    assert (late.status_code, late.json()["code"]) == (422, "INVALID_CODE")


@pytest.mark.asyncio
async def test_forgot_password_changes_it_and_ends_every_session(client, mail, emails):
    email = _email(emails)
    assert (await _verified(client, mail, email)).status_code == 200  # sessão aberta
    mail.sent.clear()

    unknown = await _post(client, "password/forgot", {"email": _email(emails)})
    assert (unknown.status_code, unknown.json()) == (202, {"status": "CODE_SENT"})
    assert mail.sent == []

    known = await _post(client, "password/forgot", {"email": email})
    assert (known.status_code, known.json()) == (202, {"status": "CODE_SENT"})
    assert [m.kind for m in mail.sent] == ["RESET"]
    code = mail.last_code()

    wrong = "000000" if code != "000000" else "111111"
    bad = {"email": email, "code": wrong, "newPassword": OTHER_PASSWORD}
    assert (await _post(client, "password/reset", bad)).json()["code"] == "INVALID_CODE"
    # Senha fraca não gasta o código.
    weak = {"email": email, "code": code, "newPassword": "fraca"}
    assert (await _post(client, "password/reset", weak)).json()["code"] == "INVALID_PASSWORD"

    good = {"email": email, "code": code, "newPassword": OTHER_PASSWORD}
    assert (await _post(client, "password/reset", good)).status_code == 204
    assert (await client.get("/api/v1/auth/session")).json()["user"] is None  # sessões encerradas
    assert (await _post(client, "password/reset", good)).json()["code"] == "INVALID_CODE"  # usado
    client.cookies.clear()
    old = await _post(client, "login", {"email": email, "password": PASSWORD})
    assert old.json()["code"] == "INVALID_CREDENTIALS"
    new = await _post(client, "login", {"email": email, "password": OTHER_PASSWORD})
    assert new.status_code == 200


@pytest.mark.asyncio
async def test_reset_also_confirms_an_unverified_account(client, mail, emails):
    email = _email(emails)
    await _register(client, email, OTHER_PASSWORD)
    await _post(client, "password/forgot", {"email": email})
    body = {"email": email, "code": mail.last_code(), "newPassword": PASSWORD}
    assert (await _post(client, "password/reset", body)).status_code == 204
    assert (await _post(client, "login", {"email": email, "password": PASSWORD})).status_code == 200


@pytest.mark.asyncio
async def test_too_many_emails_for_one_address_are_limited(client, mail, emails):
    email = _email(emails)
    for _ in range(5):
        assert (await _post(client, "password/forgot", {"email": email})).status_code == 202
    blocked = await _post(client, "password/forgot", {"email": email})
    assert (blocked.status_code, blocked.json()["code"]) == (429, "CODE_RATE_LIMITED")
    assert int(blocked.headers["retry-after"]) > 0


@pytest.mark.asyncio
async def test_without_smtp_registering_says_email_is_unavailable(session_factory, emails):
    async for c in _client(Settings(auth_required=True, smtp_host=None)):
        response = await _register(c, _email(emails))
        assert (response.status_code, response.json()["code"]) == (503, "EMAIL_NOT_CONFIGURED")


@pytest.mark.asyncio
async def test_old_unverified_accounts_leave_when_someone_registers(
    client, mail, emails, session_factory
):
    old = _email(emails)
    await _register(client, old)
    async with unit_of_work(session_factory) as session:
        await session.execute(
            update(UserModel)
            .where(UserModel.email == old)
            .values(created_at=datetime.now(UTC) - timedelta(days=8))
        )
    await _register(client, _email(emails))
    async with session_factory() as session:
        assert await session.scalar(select(UserModel).where(UserModel.email == old)) is None


@pytest.mark.asyncio
async def test_login_logout_and_identical_refusals(client, emails, session_factory):
    email = _email(emails)
    await create_verified_user(session_factory, email, PASSWORD)
    for candidate, password in ((email, "Errada-Errada-1"), (_email(emails), PASSWORD)):
        refused = await _post(client, "login", {"email": candidate, "password": password})
        assert (refused.status_code, refused.json()["code"]) == (401, "INVALID_CREDENTIALS")

    ok = await _post(client, "login", {"email": email.upper(), "password": PASSWORD})
    assert ok.status_code == 200 and ok.json()["user"]["email"] == email
    assert (await client.get("/api/v1/matches")).status_code != 401

    out = await client.post("/api/v1/auth/logout", headers=HEADERS)
    assert out.status_code == 204
    assert (await client.get("/api/v1/auth/session")).json()["user"] is None
    assert (await client.get("/api/v1/matches")).json()["code"] == "AUTH_REQUIRED"


@pytest.mark.asyncio
async def test_expired_session_is_refused(client, emails, session_factory):
    email = _email(emails)
    user_id = await create_verified_user(session_factory, email, PASSWORD)
    await _post(client, "login", {"email": email, "password": PASSWORD})
    async with unit_of_work(session_factory) as session:
        await session.execute(
            update(AuthSessionModel)
            .where(AuthSessionModel.user_id == user_id)
            .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
    response = await client.get("/api/v1/matches")
    assert (response.status_code, response.json()["code"]) == (401, "AUTH_REQUIRED")


class _FakeSmtp:
    calls: list[tuple] = []
    message = None

    def __init__(self, host, port, timeout):
        type(self).calls = [("connect", host, port, timeout)]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self, context):
        type(self).calls.append(("starttls",))

    def login(self, user, password):
        type(self).calls.append(("login", user, password))

    def send_message(self, message):
        type(self).message = message


@pytest.mark.asyncio
async def test_smtp_as_gmail_uses_starttls_and_app_password_without_spaces(monkeypatch):
    monkeypatch.setattr(mailer_module.smtplib, "SMTP", _FakeSmtp)
    settings = Settings(
        smtp_host="smtp.gmail.com",
        smtp_port=587,
        smtp_starttls=True,
        smtp_username="conta@gmail.example",
        smtp_password="abcd efgh ijkl mnop",
        smtp_from="RiftLab <conta@gmail.example>",
    )
    await SmtpMailer(settings).send(OutgoingEmail("pessoa@example.test", "VERIFY", "123456"))
    assert _FakeSmtp.calls == [
        ("connect", "smtp.gmail.com", 587, 10.0),
        ("starttls",),
        ("login", "conta@gmail.example", "abcdefghijklmnop"),
    ]
    assert _FakeSmtp.message["To"] == "pessoa@example.test"
    assert _FakeSmtp.message["From"] == "RiftLab <conta@gmail.example>"
    assert "123456" not in _FakeSmtp.message["Subject"]
    assert "123456" in _FakeSmtp.message.get_content()


@pytest.mark.asyncio
async def test_smtp_as_mailpit_has_no_tls_or_login(monkeypatch):
    monkeypatch.setattr(mailer_module.smtplib, "SMTP", _FakeSmtp)
    settings = Settings(smtp_host="mailpit", smtp_port=1025, smtp_username="", smtp_password="")
    await SmtpMailer(settings).send(OutgoingEmail("pessoa@example.test", "RESET", "654321"))
    assert _FakeSmtp.calls == [("connect", "mailpit", 1025, 10.0)]


def test_notice_email_has_no_code():
    subject, body = render(OutgoingEmail("pessoa@example.test", "ALREADY_REGISTERED"))
    assert "None" not in subject + body and "Esqueci minha senha" in body


@pytest.mark.asyncio
async def test_delivery_failure_never_logs_the_address_or_the_code(monkeypatch):
    logged: list[str] = []
    monkeypatch.setattr(
        logging.getLogger("riftlab.api"), "error", lambda msg, *args: logged.append(msg % args)
    )

    class Broken:
        async def send(self, email):
            raise RuntimeError(f"recusado: {email.to} {email.code}")

    await deliver(Broken(), OutgoingEmail("pessoa@example.test", "VERIFY", "123456"))
    assert len(logged) == 1 and "RuntimeError" in logged[0]
    assert "pessoa@" not in logged[0] and "123456" not in logged[0]


@pytest.mark.asyncio
async def test_protection_requires_session_and_csrf_header_but_not_for_health(client):
    response = await client.get("/api/v1/matches")
    assert (response.status_code, response.json()["code"]) == (401, "AUTH_REQUIRED")
    assert response.headers["content-type"].startswith("application/problem+json")
    assert (await client.get("/health/live")).status_code == 200
    assert (await client.get("/api/v1/auth/session")).json() == {"authRequired": True, "user": None}
    no_header = await client.post(
        "/api/v1/auth/login", json={"email": "qualquer@example.test", "password": PASSWORD}
    )
    assert (no_header.status_code, no_header.json()["code"]) == (403, "CSRF_HEADER_MISSING")


@pytest.mark.asyncio
async def test_too_many_failures_are_throttled(client, emails):
    email = _email(emails)
    for _ in range(5):
        await _post(client, "login", {"email": email, "password": "Errada-Errada-1"})
    blocked = await _post(client, "login", {"email": email, "password": PASSWORD})
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
