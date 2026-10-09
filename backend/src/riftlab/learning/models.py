"""CC-304: learning attempts (OPEN → ANSWERED → REVEALED, monotonic)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from riftlab.shared.db import Base, CreatedAtMixin, UuidPkMixin


class LearningAttemptModel(UuidPkMixin, CreatedAtMixin, Base):
    __tablename__ = "learning_attempts"
    __table_args__ = (
        CheckConstraint("status IN ('OPEN', 'ANSWERED', 'REVEALED')", name="status_known"),
        CheckConstraint("hint_level BETWEEN 0 AND 2", name="hint_level_range"),
    )

    decision_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("decision_points.id"), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    request_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    objective: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    options: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    reason_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="OPEN")
    selected_item_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    skipped: Mapped[bool] = mapped_column(nullable=False, default=False)
    rationale: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    hint_level: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    analysis_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("analyses.id"), nullable=True)
    revealed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    exercise_version: Mapped[str] = mapped_column(String, nullable=False)
