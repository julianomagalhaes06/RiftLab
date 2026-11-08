"""Contas de teste já confirmadas, sem passar pelo e-mail."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from argon2 import PasswordHasher
from httpx import AsyncClient

from riftlab.auth.models import UserModel
from riftlab.shared.db import unit_of_work

HEADERS = {"X-RiftLab-Request": "1"}
PASSWORD = "Senha-Sintetica-1"


def unique_email(prefix: str = "conta") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}@example.test"


async def create_verified_user(session_factory, email: str, password: str = PASSWORD) -> uuid.UUID:
    async with unit_of_work(session_factory) as session:
        now = datetime.now(UTC)
        user = UserModel(
            email=email,
            password_hash=PasswordHasher().hash(password),
            created_at=now,
            email_verified_at=now,
        )
        session.add(user)
        await session.flush()
        return user.id


async def sign_in(client: AsyncClient, session_factory, email: str) -> uuid.UUID:
    """Cria a conta confirmada e abre a sessão neste cliente."""
    user_id = await create_verified_user(session_factory, email)
    response = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": PASSWORD}, headers=HEADERS
    )
    assert response.status_code == 200, response.text
    return user_id
