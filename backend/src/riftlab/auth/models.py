from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from riftlab.shared.db import Base


class UserModel(Base):
    """Conta local. `username_key` (casefold) garante unicidade sem depender de maiúsculas."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    username: Mapped[str] = mapped_column(String(32), nullable=False)
    username_key: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Visitante (card #14): sem senha, só a demonstração, apagado depois de GUEST_LIFETIME.
    is_guest: Mapped[bool] = mapped_column(nullable=False, default=False, server_default="false")


class AuthSessionModel(Base):
    """Sessão do navegador. O token só existe no cookie; aqui fica o SHA-256 dele."""

    __tablename__ = "auth_sessions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RiotLinkChallengeModel(Base):
    """Desafio do ícone em andamento (card #12): um por usuário. O Riot ID canônico fica só
    enquanto o desafio existe; verificar, recomeçar ou expirar o substitui ou apaga."""

    __tablename__ = "riot_link_challenges"

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    account_key: Mapped[str] = mapped_column(String(64), nullable=False)
    game_name: Mapped[str] = mapped_column(String(32), nullable=False)
    tag_line: Mapped[str] = mapped_column(String(8), nullable=False)
    icon_id: Mapped[int] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RiotLinkModel(Base):
    """Conta Riot vinculada e comprovada. `account_key` (HMAC do Riot ID) cruza com o elo e a
    sincronização e é única: uma conta Riot pertence a um usuário só. O Riot ID do próprio
    usuário fica guardado porque a Riot só é consultada por ele; sai ao desvincular."""

    __tablename__ = "riot_links"

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    account_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    subject_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    game_name: Mapped[str] = mapped_column(String(32), nullable=False)
    tag_line: Mapped[str] = mapped_column(String(8), nullable=False)
    linked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
