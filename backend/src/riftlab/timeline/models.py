from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from riftlab.shared.db import Base, CreatedAtMixin, UuidPkMixin


class TimelineEventModel(Base):
    __tablename__ = "timeline_events"
    __table_args__ = (
        ForeignKeyConstraint(["match_id"], ["matches.match_id"], name="fk_timeline_events_match"),
        CheckConstraint("timestamp_ms >= 0", name="timestamp_non_negative"),
        Index("ix_timeline_events_match_ts", "match_id", "timestamp_ms", "source_index"),
    )

    match_id: Mapped[str] = mapped_column(String, primary_key=True)
    source_index: Mapped[int] = mapped_column(Integer, primary_key=True)
    timestamp_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    type: Mapped[str] = mapped_column(String, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    schema_version: Mapped[str] = mapped_column(String, nullable=False, default="v1")


class MatchReconstructionModel(CreatedAtMixin, Base):
    """Marca de conclusão atômica de uma reconstrução (CC-203, revisão):
    existe independente de contagem de compra de qualquer participante."""

    __tablename__ = "match_reconstructions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["match_id"], ["matches.match_id"], name="fk_match_reconstructions_match"
        ),
        ForeignKeyConstraint(
            ["catalog_id"], ["catalog_versions.id"], name="fk_match_reconstructions_catalog"
        ),
    )

    match_id: Mapped[str] = mapped_column(String, primary_key=True)
    catalog_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)


class DecisionStateModel(UuidPkMixin, CreatedAtMixin, Base):
    __tablename__ = "decision_states"
    __table_args__ = (
        UniqueConstraint(
            "match_id",
            "participant_id",
            "event_source_index",
            "state_version",
            name="uq_decision_states_identity",
        ),
        ForeignKeyConstraint(
            ["match_id", "participant_id"],
            ["participants.match_id", "participants.participant_id"],
            name="fk_decision_states_participant",
        ),
        ForeignKeyConstraint(
            ["match_id", "event_source_index"],
            ["timeline_events.match_id", "timeline_events.source_index"],
            name="fk_decision_states_event",
        ),
        ForeignKeyConstraint(
            ["catalog_id"], ["catalog_versions.id"], name="fk_decision_states_catalog"
        ),
        Index("ix_decision_states_match_participant", "match_id", "participant_id"),
    )

    match_id: Mapped[str] = mapped_column(String, nullable=False)
    participant_id: Mapped[int] = mapped_column(Integer, nullable=False)
    event_source_index: Mapped[int] = mapped_column(Integer, nullable=False)
    state_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    features: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    quality: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    schema_version: Mapped[str] = mapped_column(String, nullable=False, default="v1")
    catalog_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
