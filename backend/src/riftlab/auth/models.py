from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from riftlab.shared.db import Base


class UserModel(Base):
    """Conta local por e-mail (em minúsculas, único). Sem `email_verified_at`, não entra. Quem
    só usou o login social não tem senha local (`password_hash` nulo)."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(254), nullable=False, unique=True)
    email_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AuthSocialIdentityModel(Base):
    """Conta do provedor (Google ou Discord) ligada a um usuário. `subject` é o identificador
    estável do provedor, que continua valendo se o e-mail lá mudar."""

    __tablename__ = "auth_social_identities"
    __table_args__ = (CheckConstraint("provider IN ('google','discord')", name="provider_valid"),)

    provider: Mapped[str] = mapped_column(String(16), primary_key=True)
    subject: Mapped[str] = mapped_column(String(255), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AuthEmailCodeModel(Base):
    """Código enviado por e-mail: no máximo um vivo por conta e finalidade (VERIFY ou RESET).
    O banco guarda só o hash; errar demais ou vencer apaga o código."""

    __tablename__ = "auth_email_codes"
    __table_args__ = (CheckConstraint("purpose IN ('VERIFY','RESET')", name="purpose_valid"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    purpose: Mapped[str] = mapped_column(String(16), primary_key=True)
    code_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


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
