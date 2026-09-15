from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from riftlab.shared.db import Base, CreatedAtMixin, UuidPkMixin


class MatchModel(Base):
    __tablename__ = "matches"
    __table_args__ = (CheckConstraint("duration_seconds >= 0", name="duration_non_negative"),)

    match_id: Mapped[str] = mapped_column(String, primary_key=True)
    queue: Mapped[int] = mapped_column(Integer, nullable=False)
    map_id: Mapped[int] = mapped_column("map", Integer, nullable=False)
    patch: Mapped[str] = mapped_column(String, nullable=False)
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    raw_hash: Mapped[str] = mapped_column(String, nullable=False)


class ParticipantModel(Base):
    __tablename__ = "participants"

    match_id: Mapped[str] = mapped_column(ForeignKey("matches.match_id"), primary_key=True)
    participant_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    champion_id: Mapped[int] = mapped_column(Integer, nullable=False)
    team: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str | None] = mapped_column(String, nullable=True)
    subject_hash: Mapped[str] = mapped_column(String, nullable=False)


class ImportJobModel(UuidPkMixin, CreatedAtMixin, Base):
    __tablename__ = "import_jobs"
    __table_args__ = (
        CheckConstraint("attempts >= 0", name="attempts_non_negative"),
        CheckConstraint("lease_generation >= 0", name="lease_generation_non_negative"),
        CheckConstraint("mode IN ('DEMO','RIOT')", name="mode_valid"),
        CheckConstraint(
            "state IN ('QUEUED','RUNNING','RETRY_WAIT','SUCCEEDED','FAILED')",
            name="state_valid",
        ),
        # Identidade ativa: no máximo um job em estado ativo por (match solicitado, modo).
        Index(
            "ux_import_jobs_active_identity",
            "requested_match_id",
            "mode",
            unique=True,
            postgresql_where=text("state IN ('QUEUED','RUNNING','RETRY_WAIT')"),
        ),
        Index("ix_import_jobs_state_retry_at", "state", "retry_at"),
    )

    requested_match_id: Mapped[str] = mapped_column(String, nullable=False)
    match_id: Mapped[str | None] = mapped_column(ForeignKey("matches.match_id"), nullable=True)
    mode: Mapped[str] = mapped_column(String, nullable=False)
    state: Mapped[str] = mapped_column(String, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Token de posse (CC-201): incrementado a cada reserva (fresca, retomada de
    # RETRY_WAIT ou reclamada de lease vencido). Toda escrita final (heartbeat,
    # SUCCEEDED/FAILED/RETRY_WAIT) é condicionada a WHERE lease_generation=<a que
    # o worker recebeu na reserva> — um worker que perdeu a posse (job já
    # reclamado por outro) tem 0 linhas afetadas e descarta o resultado, nunca
    # sobrescreve. Ver docs/architecture.md (lease 120s, heartbeat 30s).
    lease_generation: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String, nullable=True)
    # Retry manual (POST /imports/{id}/retry) cria uma nova linha vinculada à
    # tentativa anterior (histórico preservado, nunca sobrescrito); None para a
    # primeira tentativa de uma identidade.
    retry_of_job_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("import_jobs.id"), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
