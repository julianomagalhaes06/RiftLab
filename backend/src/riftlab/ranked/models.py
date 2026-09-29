from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Index, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from riftlab.shared.db import Base


class RankedObservationModel(Base):
    __tablename__ = "ranked_observations"
    __table_args__ = (
        CheckConstraint("state IN ('READY','UNRANKED','FAILED')", name="ranked_state_valid"),
        CheckConstraint("league_points IS NULL OR league_points >= 0", name="ranked_lp_positive"),
        Index(
            "ix_ranked_account_queue_season_time",
            "account_key",
            "queue",
            "season",
            "captured_at",
            "id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    subject_hash: Mapped[str | None] = mapped_column(String(64))
    account_key: Mapped[str] = mapped_column(String(64), nullable=False)
    queue: Mapped[str] = mapped_column(String(32), nullable=False)
    season: Mapped[str] = mapped_column(String(20), nullable=False)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    state: Mapped[str] = mapped_column(String(12), nullable=False)
    tier: Mapped[str | None] = mapped_column(String(16))
    division: Mapped[str | None] = mapped_column(String(4))
    league_points: Mapped[int | None] = mapped_column(Integer)
    wins: Mapped[int | None] = mapped_column(Integer)
    losses: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(40))
    origin: Mapped[str] = mapped_column(String(30), nullable=False)
