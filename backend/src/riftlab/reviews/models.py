"""CC-505: reviewer judgements on an analysis (immutable, idempotent)."""

from __future__ import annotations

import uuid

from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

import riftlab.analyses.models  # noqa: F401  (alvo da FK analyses)
from riftlab.shared.db import Base, CreatedAtMixin, UuidPkMixin


class AnalysisFeedbackModel(UuidPkMixin, CreatedAtMixin, Base):
    __tablename__ = "analysis_feedback"
    __table_args__ = (
        UniqueConstraint(
            "analysis_id",
            "item_id",
            "reviewer_hash",
            "rubric_version",
            name="uq_analysis_feedback_judgement",
        ),
        CheckConstraint("grade IS NULL OR grade BETWEEN 0 AND 3", name="grade_range"),
    )

    analysis_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("analyses.id"), nullable=False)
    item_id: Mapped[int] = mapped_column(Integer, nullable=False)
    reviewer_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    rubric_version: Mapped[str] = mapped_column(String(40), nullable=False)
    grade: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reason: Mapped[str] = mapped_column(String(1000), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    request_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
