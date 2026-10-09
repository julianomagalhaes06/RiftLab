"""CC-303: persisted, immutable analyses (input hash, engine, versions, result)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

import riftlab.decisions.models  # noqa: F401  (alvo da FK decision_points)
import riftlab.ml.models  # noqa: F401  (alvo da FK ml_model_versions)
from riftlab.shared.db import Base, CreatedAtMixin, UuidPkMixin


class AnalysisModel(UuidPkMixin, CreatedAtMixin, Base):
    __tablename__ = "analyses"

    decision_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("decision_points.id"), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    request_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    engine: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    model_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("ml_model_versions.id"), nullable=True
    )
    result: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
