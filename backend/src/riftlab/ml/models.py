"""CC-405: persistence of dataset manifests, model versions and promotion events."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from riftlab.shared.db import Base, CreatedAtMixin, UuidPkMixin


class DatasetManifestModel(UuidPkMixin, CreatedAtMixin, Base):
    __tablename__ = "ml_dataset_manifests"

    sha256: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    task: Mapped[str] = mapped_column(String, nullable=False)
    patch: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(String, nullable=False)
    # CC-603: partidas do dataset conhecidas (lista registrada na criação) e invalidação
    # quando alguma delas é expurgada; modelos desse dataset não podem mais ser promovidos.
    match_list_known: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    invalidated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    invalidation_reason: Mapped[str | None] = mapped_column(String(40))


class DatasetManifestMatchModel(Base):
    __tablename__ = "ml_dataset_manifest_matches"
    __table_args__ = (Index("ix_ml_dataset_manifest_matches_match_id", "match_id"),)

    manifest_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ml_dataset_manifests.id"), primary_key=True
    )
    match_id: Mapped[str] = mapped_column(String, primary_key=True)


class ModelVersionModel(UuidPkMixin, CreatedAtMixin, Base):
    __tablename__ = "ml_model_versions"
    __table_args__ = (
        CheckConstraint("kind IN ('BASELINE', 'MODEL')", name="kind_known"),
        CheckConstraint("status IN ('REGISTERED', 'ACTIVE', 'RETIRED')", name="status_known"),
        CheckConstraint(
            "(kind = 'MODEL') = (artifact_path IS NOT NULL)", name="artifact_iff_model"
        ),
        Index(
            "uq_ml_model_versions_one_active",
            "task",
            "scope",
            unique=True,
            postgresql_where=text("status = 'ACTIVE'"),
        ),
    )

    task: Mapped[str] = mapped_column(String, nullable=False)
    scope: Mapped[str] = mapped_column(String, nullable=False)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    artifact_path: Mapped[str | None] = mapped_column(String, nullable=True)
    artifact_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    feature_names: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    dataset_manifest_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ml_dataset_manifests.id"), nullable=False
    )
    status: Mapped[str] = mapped_column(String, nullable=False, default="REGISTERED")
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ModelEventModel(UuidPkMixin, CreatedAtMixin, Base):
    __tablename__ = "ml_model_events"
    __table_args__ = (
        CheckConstraint(
            "action IN ('REGISTER', 'PROMOTE', 'RETIRE', 'ROLLBACK', 'INVALIDATE')",
            name="action_known",
        ),
    )

    model_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ml_model_versions.id"), nullable=False
    )
    action: Mapped[str] = mapped_column(String, nullable=False)
    detail: Mapped[str] = mapped_column(String, nullable=False, default="")
