"""Cadastro, login e sessões (card #10).

Senha com Argon2id (parâmetros padrão do argon2-cffi), calculada fora do laço de eventos.
O token de sessão (256 bits aleatórios) só vai no cookie; o banco guarda o SHA-256 dele, e
o logout apaga a linha. Usuário inexistente e senha errada têm a mesma resposta e o mesmo
custo de verificação.
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

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from riftlab.auth.models import AuthSessionModel, UserModel
from riftlab.shared.db import unit_of_work

SessionFactory = async_sessionmaker[AsyncSession]
USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")
PASSWORD_MIN, PASSWORD_MAX = 10, 128
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
    username: str


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def validate_credentials(username: str, password: str) -> None:
    if not USERNAME_PATTERN.fullmatch(username):
        raise AuthError("INVALID_USERNAME")
    if not PASSWORD_MIN <= len(password) <= PASSWORD_MAX:
        raise AuthError("INVALID_PASSWORD")


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


async def register(
    factory: SessionFactory, username: str, password: str, *, lifetime: timedelta
) -> tuple[AuthUser, str]:
    """Cria a conta e já abre uma sessão. Nome repetido (sem diferenciar maiúsculas) ⇒ erro."""
    validate_credentials(username, password)
    password_hash = await asyncio.to_thread(_HASHER.hash, password)
    now = datetime.now(UTC)
    try:
        async with unit_of_work(factory) as session:
            user = UserModel(
                username=username,
                username_key=username.casefold(),
                password_hash=password_hash,
                created_at=now,
            )
            session.add(user)
            await session.flush()
            token = await _new_session(session, user.id, lifetime, now)
            return AuthUser(user.id, user.username), token
    except IntegrityError as exc:
        raise AuthError("USERNAME_TAKEN") from exc


async def login(
    factory: SessionFactory, username: str, password: str, *, lifetime: timedelta
) -> tuple[AuthUser, str]:
    async with factory() as session:
        user = await session.scalar(
            select(UserModel).where(UserModel.username_key == username.casefold())
        )
    stored = user.password_hash if user is not None else _DUMMY_HASH
    try:
        await asyncio.to_thread(_HASHER.verify, stored, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError) as exc:
        raise AuthError("INVALID_CREDENTIALS") from exc
    if user is None:
        raise AuthError("INVALID_CREDENTIALS")
    now = datetime.now(UTC)
    async with unit_of_work(factory) as session:
        if _HASHER.check_needs_rehash(user.password_hash):
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
    return AuthUser(user.id, user.username), token


async def user_for_token(factory: SessionFactory, token: str | None) -> AuthUser | None:
    """Usuário da sessão válida; token ausente, desconhecido ou vencido ⇒ None."""
    if not token or len(token) > 128:
        return None
    async with factory() as session:
        row = (
            await session.execute(
                select(UserModel.id, UserModel.username)
                .join(AuthSessionModel, AuthSessionModel.user_id == UserModel.id)
                .where(
                    AuthSessionModel.token_hash == token_hash(token),
                    AuthSessionModel.expires_at > datetime.now(UTC),
                )
            )
        ).first()
    return AuthUser(row.id, row.username) if row else None


async def logout(factory: SessionFactory, token: str | None) -> None:
    if not token:
        return
    async with unit_of_work(factory) as session:
        await session.execute(
            delete(AuthSessionModel).where(AuthSessionModel.token_hash == token_hash(token))
        )


class LoginThrottle:
    """Limite de falhas de login numa janela deslizante, por usuário e por IP.

    Em memória, do processo da API (uma instância por estação): reiniciar zera a contagem.
    Passou do limite ⇒ LOGIN_RATE_LIMITED com o tempo até a falha mais antiga sair da janela.
    """

    def __init__(
        self,
        *,
        per_user: int = 5,
        per_ip: int = 20,
        window_seconds: float = 900.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.per_user, self.per_ip, self.window = per_user, per_ip, window_seconds
        self._now = clock
        self._failures: dict[str, deque[float]] = {}

    def _recent(self, key: str, now: float) -> deque[float]:
        entries = self._failures.setdefault(key, deque())
        while entries and entries[0] <= now - self.window:
            entries.popleft()
        return entries

    def check(self, username: str, ip: str) -> None:
        now = self._now()
        for key, limit in ((f"u:{username.casefold()}", self.per_user), (f"i:{ip}", self.per_ip)):
            entries = self._recent(key, now)
            if len(entries) >= limit:
                wait = int(entries[0] + self.window - now) + 1
                raise AuthError("LOGIN_RATE_LIMITED", retry_after_seconds=wait)

    def failed(self, username: str, ip: str) -> None:
        now = self._now()
        for key in (f"u:{username.casefold()}", f"i:{ip}"):
            self._recent(key, now).append(now)

    def succeeded(self, username: str) -> None:
        self._failures.pop(f"u:{username.casefold()}", None)
