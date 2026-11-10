"""Cadastro por e-mail confirmado, login, recuperação de senha e sessões (cards #10/#11).

Senha com Argon2id (parâmetros padrão do argon2-cffi), calculada fora do laço de eventos.
O token de sessão (256 bits aleatórios) só vai no cookie; o banco guarda o SHA-256 dele, e
o logout apaga a linha. E-mail inexistente e senha errada têm a mesma resposta e o mesmo
custo de verificação.

Cadastro, reenvio e "esqueci minha senha" respondem igual exista ou não a conta: quem manda
o e-mail é a rota, em segundo plano. O código tem 6 dígitos, vale 15 minutos, aguenta 5
tentativas e fica só como hash. Confirmar o e-mail também exige a senha do cadastro: sem isso,
quem cadastrasse primeiro um e-mail alheio poderia ver a própria senha ativada pelo dono.

O login social (`social_login`) só recebe e-mail confirmado pelo provedor. Pelo mesmo motivo do
parágrafo acima, um cadastro local ainda não confirmado perde a senha quando o dono do e-mail
chega pelo provedor. O resultado diz se a conta nasceu agora, se o provedor acabou de ser ligado
a uma conta que já existia (a rota avisa o dono por e-mail) ou se foi só mais uma entrada.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import secrets
import time
import uuid
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from riftlab.auth.mailer import OutgoingEmail
from riftlab.auth.models import (
    AuthEmailCodeModel,
    AuthSessionModel,
    AuthSocialIdentityModel,
    UserModel,
)
from riftlab.shared.db import unit_of_work

SessionFactory = async_sessionmaker[AsyncSession]
EMAIL_MAX = 254
EMAIL_PATTERN = re.compile(r"^[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9-]+(\.[a-z0-9-]+)+$")
CODE_PATTERN = re.compile(r"^[0-9]{6}$")
PASSWORD_MIN, PASSWORD_MAX = 8, 128
NAME_MIN, NAME_MAX = 2, 40
CODE_LIFETIME = timedelta(minutes=15)
CODE_RESEND_AFTER = timedelta(seconds=60)
CODE_MAX_ATTEMPTS = 5
# Cadastro nunca confirmado sai depois disso: o e-mail de quem não confirmou não fica guardado.
UNVERIFIED_RETENTION = timedelta(days=7)
VERIFY, RESET = "VERIFY", "RESET"
_HASHER = PasswordHasher()
# Hash de uma senha qualquer: usuário inexistente custa o mesmo que senha errada.
_DUMMY_HASH = _HASHER.hash(secrets.token_urlsafe(16))


class AuthError(Exception):
    """Erro de autenticação com código estável (mensagem fixa no handler)."""

    def __init__(self, code: str, *, retry_after_seconds: int | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.retry_after_seconds = retry_after_seconds


@dataclass(frozen=True, slots=True)
class AuthUser:
    id: uuid.UUID
    email: str
    name: str | None = None


def _auth_user(user: UserModel) -> AuthUser:
    return AuthUser(user.id, user.email, user.display_name)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def normalize_email(email: str) -> str:
    return email.strip().lower()


def validate_email(email: str) -> None:
    if len(email) > EMAIL_MAX or not EMAIL_PATTERN.fullmatch(email):
        raise AuthError("INVALID_EMAIL")


def validate_password(password: str) -> None:
    """De 8 a 128 caracteres, com ao menos uma maiúscula e um símbolo."""
    if (
        not PASSWORD_MIN <= len(password) <= PASSWORD_MAX
        or not any(c.isupper() for c in password)
        or not any(not c.isalnum() and not c.isspace() for c in password)
    ):
        raise AuthError("INVALID_PASSWORD")


def clean_name(raw: str) -> str:
    """Nome com os espaços normalizados; de 2 a 40 caracteres, sem caracteres de controle."""
    name = " ".join(raw.split())
    if not NAME_MIN <= len(name) <= NAME_MAX or not name.isprintable():
        raise AuthError("INVALID_NAME")
    return name


async def _new_session(
    session: AsyncSession, user_id: uuid.UUID, lifetime: timedelta, now: datetime
) -> str:
    token = secrets.token_urlsafe(32)
    session.add(
        AuthSessionModel(
            user_id=user_id, token_hash=token_hash(token), created_at=now, expires_at=now + lifetime
        )
    )
    return token


async def _issue_code(
    session: AsyncSession, user_id: uuid.UUID, purpose: str, now: datetime
) -> str | None:
    """Novo código (substitui o anterior) ou None se o último saiu há menos de um minuto."""
    row = await session.get(AuthEmailCodeModel, (user_id, purpose))
    if row is not None and now - row.sent_at < CODE_RESEND_AFTER:
        return None
    code = f"{secrets.randbelow(10**6):06d}"
    code_hash = await asyncio.to_thread(_HASHER.hash, code)
    if row is None:
        row = AuthEmailCodeModel(user_id=user_id, purpose=purpose)
        session.add(row)
    row.code_hash, row.attempts = code_hash, 0
    row.sent_at, row.expires_at = now, now + CODE_LIFETIME
    return code


async def _user_by_email(session: AsyncSession, email: str) -> UserModel | None:
    """Com o lock da linha: pedidos simultâneos da mesma conta entram um de cada vez."""
    return await session.scalar(select(UserModel).where(UserModel.email == email).with_for_update())


async def _consume_code(
    session: AsyncSession, user: UserModel | None, purpose: str, code: str, now: datetime
) -> bool:
    """Confere o código. Certo: apaga e devolve True. Errado: conta a tentativa; vencido ou na
    última tentativa, apaga. Sem conta ou sem código, gasta o mesmo tempo de uma verificação."""
    row = (
        await session.scalar(
            select(AuthEmailCodeModel)
            .where(AuthEmailCodeModel.user_id == user.id, AuthEmailCodeModel.purpose == purpose)
            .with_for_update()
        )
        if user is not None
        else None
    )
    usable = row is not None and row.expires_at > now
    try:
        await asyncio.to_thread(
            _HASHER.verify, row.code_hash if row and usable else _DUMMY_HASH, code
        )
        matched = usable
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        matched = False
    if row is None:
        return False
    if matched:
        await session.delete(row)
        return True
    row.attempts += 1
    if not usable or row.attempts >= CODE_MAX_ATTEMPTS:
        await session.delete(row)
    return False


async def register(
    factory: SessionFactory, email: str, password: str, name: str
) -> OutgoingEmail | None:
    """Cria a conta não confirmada e prepara o código. O que volta é o e-mail a enviar, sem
    dizer ao chamador se a conta existia: confirmada ⇒ aviso de conta existente; ainda não
    confirmada ⇒ o nome e a senha novos valem e sai outro código (respeitado o intervalo de
    reenvio)."""
    email = normalize_email(email)
    validate_email(email)
    name = clean_name(name)
    validate_password(password)
    password_hash = await asyncio.to_thread(_HASHER.hash, password)
    now = datetime.now(UTC)
    try:
        async with unit_of_work(factory) as session:
            await session.execute(
                delete(UserModel).where(
                    UserModel.email_verified_at.is_(None),
                    UserModel.created_at <= now - UNVERIFIED_RETENTION,
                )
            )
            user = await _user_by_email(session, email)
            if user is None:
                user = UserModel(
                    email=email, password_hash=password_hash, display_name=name, created_at=now
                )
                session.add(user)
                await session.flush()
            elif user.email_verified_at is not None:
                return OutgoingEmail(email, "ALREADY_REGISTERED")
            else:
                user.password_hash, user.display_name = password_hash, name
            code = await _issue_code(session, user.id, VERIFY, now)
    except IntegrityError:
        return None  # outro pedido criou a mesma conta agora; o e-mail é o dele
    return OutgoingEmail(email, "VERIFY", code) if code is not None else None


async def _password_matches(user: UserModel | None, password: str) -> bool:
    # Conta sem senha local (só login social) custa o mesmo que senha errada e nunca confere.
    stored = user.password_hash if user is not None else None
    try:
        await asyncio.to_thread(_HASHER.verify, stored or _DUMMY_HASH, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False
    return stored is not None


async def login(
    factory: SessionFactory, email: str, password: str, *, lifetime: timedelta
) -> tuple[AuthUser, str]:
    """Só depois da senha certa a resposta revela que a conta falta confirmar."""
    email = normalize_email(email)
    async with factory() as session:
        user = await session.scalar(select(UserModel).where(UserModel.email == email))
    if not await _password_matches(user, password) or user is None:
        raise AuthError("INVALID_CREDENTIALS")
    if user.email_verified_at is None:
        raise AuthError("EMAIL_NOT_VERIFIED")
    now = datetime.now(UTC)
    async with unit_of_work(factory) as session:
        if user.password_hash and _HASHER.check_needs_rehash(user.password_hash):
            fresh = await session.get(UserModel, user.id)
            if fresh is not None:
                fresh.password_hash = await asyncio.to_thread(_HASHER.hash, password)
        # Sessões vencidas do usuário saem a cada login.
        await session.execute(
            delete(AuthSessionModel).where(
                AuthSessionModel.user_id == user.id, AuthSessionModel.expires_at <= now
            )
        )
        token = await _new_session(session, user.id, lifetime, now)
    return _auth_user(user), token


SocialOutcome = Literal["CREATED", "LINKED", "SIGNED_IN"]


async def social_login(
    factory: SessionFactory,
    provider: str,
    subject: str,
    email: str,
    name: str | None,
    *,
    lifetime: timedelta,
) -> tuple[AuthUser, str, SocialOutcome]:
    """Abre a sessão de quem voltou do provedor com o e-mail confirmado. Devolve o usuário, o
    token e o que aconteceu com a conta. A identidade já ligada vale mesmo com outro e-mail;
    sem ela, a conta é achada pelo e-mail e ligada ao provedor (LINKED) ou, sem conta, criada
    já confirmada e sem senha (CREATED)."""
    email = normalize_email(email)
    validate_email(email)
    now = datetime.now(UTC)
    outcome: SocialOutcome = "SIGNED_IN"
    try:
        async with unit_of_work(factory) as session:
            identity = await session.get(AuthSocialIdentityModel, (provider, subject))
            user = await session.get(UserModel, identity.user_id) if identity else None
            if user is None:
                user = await _user_by_email(session, email)
                outcome = "LINKED"
                if user is None:
                    user = UserModel(email=email, email_verified_at=now, created_at=now)
                    session.add(user)
                    await session.flush()
                    outcome = "CREATED"
                elif user.email_verified_at is None:
                    # Quem digitou essa senha pode não ser o dono do e-mail, que chegou agora.
                    user.password_hash, user.email_verified_at = None, now
                    await session.execute(
                        delete(AuthEmailCodeModel).where(AuthEmailCodeModel.user_id == user.id)
                    )
                user.display_name = user.display_name or name
                session.add(
                    AuthSocialIdentityModel(
                        provider=provider, subject=subject, user_id=user.id, created_at=now
                    )
                )
            await session.execute(
                delete(AuthSessionModel).where(
                    AuthSessionModel.user_id == user.id, AuthSessionModel.expires_at <= now
                )
            )
            token = await _new_session(session, user.id, lifetime, now)
            opened = _auth_user(user)
    except IntegrityError as exc:
        # Dois retornos simultâneos na primeira entrada: quem perdeu tenta de novo e acha a conta.
        raise AuthError("SOCIAL_FAILED") from exc
    return opened, token, outcome


async def verify_email(
    factory: SessionFactory, email: str, code: str, password: str, *, lifetime: timedelta
) -> tuple[AuthUser, str]:
    """Código e senha do cadastro certos: confirma o e-mail e já abre a sessão."""
    email = normalize_email(email)
    if not CODE_PATTERN.fullmatch(code):
        raise AuthError("INVALID_CODE")
    now = datetime.now(UTC)
    opened: tuple[AuthUser, str] | None = None
    async with unit_of_work(factory) as session:
        user = await _user_by_email(session, email)
        if (
            await _password_matches(user, password)
            and user is not None
            and user.email_verified_at is None
            and await _consume_code(session, user, VERIFY, code, now)
        ):
            user.email_verified_at = now
            opened = (_auth_user(user), await _new_session(session, user.id, lifetime, now))
    # Fora do bloco: a tentativa errada precisa ser gravada antes de recusar.
    if opened is None:
        raise AuthError("INVALID_CODE")
    return opened


async def resend_verification(factory: SessionFactory, email: str) -> OutgoingEmail | None:
    email = normalize_email(email)
    now = datetime.now(UTC)
    async with unit_of_work(factory) as session:
        user = await _user_by_email(session, email)
        if user is None or user.email_verified_at is not None:
            return None
        code = await _issue_code(session, user.id, VERIFY, now)
    return OutgoingEmail(email, "VERIFY", code) if code is not None else None


async def forgot_password(factory: SessionFactory, email: str) -> OutgoingEmail | None:
    email = normalize_email(email)
    now = datetime.now(UTC)
    async with unit_of_work(factory) as session:
        user = await _user_by_email(session, email)
        if user is None:
            return None
        code = await _issue_code(session, user.id, RESET, now)
    return OutgoingEmail(email, "RESET", code) if code is not None else None


async def reset_password(factory: SessionFactory, email: str, code: str, new_password: str) -> None:
    """Código certo troca a senha e encerra todas as sessões da conta. Receber o código prova
    a posse do e-mail, então uma conta ainda não confirmada fica confirmada."""
    email = normalize_email(email)
    validate_password(new_password)
    if not CODE_PATTERN.fullmatch(code):
        raise AuthError("INVALID_CODE")
    password_hash = await asyncio.to_thread(_HASHER.hash, new_password)
    now = datetime.now(UTC)
    changed = False
    async with unit_of_work(factory) as session:
        user = await _user_by_email(session, email)
        if await _consume_code(session, user, RESET, code, now) and user is not None:
            user.password_hash = password_hash
            user.email_verified_at = user.email_verified_at or now
            await session.execute(
                delete(AuthSessionModel).where(AuthSessionModel.user_id == user.id)
            )
            changed = True
    if not changed:
        raise AuthError("INVALID_CODE")


async def user_for_token(factory: SessionFactory, token: str | None) -> AuthUser | None:
    """Usuário da sessão válida; token ausente, desconhecido ou vencido ⇒ None."""
    if not token or len(token) > 128:
        return None
    async with factory() as session:
        row = (
            await session.execute(
                select(UserModel.id, UserModel.email, UserModel.display_name)
                .join(AuthSessionModel, AuthSessionModel.user_id == UserModel.id)
                .where(
                    AuthSessionModel.token_hash == token_hash(token),
                    AuthSessionModel.expires_at > datetime.now(UTC),
                )
            )
        ).first()
    return AuthUser(row.id, row.email, row.display_name) if row else None


async def logout(factory: SessionFactory, token: str | None) -> None:
    if not token:
        return
    async with unit_of_work(factory) as session:
        await session.execute(
            delete(AuthSessionModel).where(AuthSessionModel.token_hash == token_hash(token))
        )


class LoginThrottle:
    """Limite de eventos numa janela deslizante, por conta (e-mail) e por IP: falhas de login,
    códigos errados e pedidos de e-mail, cada um na sua instância.

    Em memória, do processo da API (uma instância por estação): reiniciar zera a contagem.
    Passou do limite ⇒ `code` com o tempo até o evento mais antigo sair da janela.
    """

    def __init__(
        self,
        *,
        per_user: int = 5,
        per_ip: int = 20,
        window_seconds: float = 900.0,
        code: str = "LOGIN_RATE_LIMITED",
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.per_user, self.per_ip, self.window = per_user, per_ip, window_seconds
        self.code = code
        self._now = clock
        self._failures: dict[str, deque[float]] = {}

    def _recent(self, key: str, now: float) -> deque[float]:
        entries = self._failures.setdefault(key, deque())
        while entries and entries[0] <= now - self.window:
            entries.popleft()
        return entries

    def check(self, identity: str, ip: str) -> None:
        now = self._now()
        for key, limit in ((f"u:{identity.casefold()}", self.per_user), (f"i:{ip}", self.per_ip)):
            entries = self._recent(key, now)
            if len(entries) >= limit:
                wait = int(entries[0] + self.window - now) + 1
                raise AuthError(self.code, retry_after_seconds=wait)

    def failed(self, identity: str, ip: str) -> None:
        now = self._now()
        for key in (f"u:{identity.casefold()}", f"i:{ip}"):
            self._recent(key, now).append(now)

    def succeeded(self, identity: str) -> None:
        self._failures.pop(f"u:{identity.casefold()}", None)
