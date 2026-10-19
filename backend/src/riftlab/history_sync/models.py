"""Execuções da sincronização do histórico (CC-901): uma linha por pedido, sem PUUID."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Float, Index, Integer, String, Uuid, func, text
from sqlalchemy.orm import Mapped, mapped_column

from riftlab.shared.db import Base


class HistorySyncRunModel(Base):
    __tablename__ = "history_sync_runs"
    __table_args__ = (
        CheckConstraint(
            "state IN ('QUEUED','RUNNING','SUCCEEDED','FAILED')", name="history_sync_state"
        ),
        CheckConstraint(
            "listed >= 0 AND new_enqueued >= 0 AND already_known >= 0 AND skipped_purged >= 0",
            name="history_sync_counts",
        ),
        Index("ix_history_sync_account_requested", "account_key", "requested_at"),
        # Uma execução ativa por conta; a perdedora de uma corrida relê a vencedora.
        Index(
            "uq_history_sync_active",
            "account_key",
            unique=True,
            postgresql_where=text("state IN ('QUEUED','RUNNING')"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    account_key: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(12), nullable=False)
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.clock_timestamp()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(40))
    retry_after_seconds: Mapped[float | None] = mapped_column(Float)
    listed: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    new_enqueued: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    already_known: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    skipped_purged: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
