"""Tabelas de extração de decisões (CC-204). Linhas IMUTÁVEIS: nenhuma
operação de update (o repositório não expõe nenhuma e um gatilho no banco
rejeita UPDATE). Versões novas geram linhas novas; as antigas ficam
preservadas. `decision_states` (CC-203) nunca é reescrita."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import (
    Boolean,
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


class DecisionExtractionModel(UuidPkMixin, CreatedAtMixin, Base):
    """Marca de extração por (partida, tupla de versões). A tupla tem colunas
    ANULÁVEIS (classificação/curadoria/catálogo, nulas quando a partida foi
    reconstruída sem catálogo): no PostgreSQL, NULLs são distintos em UNIQUE
    por padrão, o que permitiria marcas duplicadas. Por isso a restrição usa
    NULLS NOT DISTINCT (PostgreSQL 15+; o compose usa 17)."""

    __tablename__ = "decision_extractions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["match_id"], ["matches.match_id"], name="fk_decision_extractions_match"
        ),
        ForeignKeyConstraint(
            ["match_id", "reconstruction_version"],
            ["match_reconstructions.match_id", "match_reconstructions.reconstruction_version"],
            name="fk_decision_extractions_reconstruction",
        ),
        ForeignKeyConstraint(
            ["catalog_id"], ["catalog_versions.id"], name="fk_decision_extractions_catalog"
        ),
        UniqueConstraint(
            "match_id",
            "reconstruction_version",
            "selection_version",
            "feature_schema_version",
            "scope_version",
            "classification_version",
            "curation_rules_version",
            "catalog_id",
            name="uq_decision_extractions_version_tuple",
            postgresql_nulls_not_distinct=True,
        ),
        CheckConstraint(
            "point_count >= 0 AND eligible_count >= 0 AND eligible_count <= point_count",
            name="counts_consistent",
        ),
    )

    match_id: Mapped[str] = mapped_column(String, nullable=False)
    reconstruction_version: Mapped[int] = mapped_column(Integer, nullable=False)
    selection_version: Mapped[str] = mapped_column(String, nullable=False)
    feature_schema_version: Mapped[str] = mapped_column(String, nullable=False)
    scope_version: Mapped[str] = mapped_column(String, nullable=False)
    classification_version: Mapped[str | None] = mapped_column(String, nullable=True)
    curation_rules_version: Mapped[str | None] = mapped_column(String, nullable=True)
    catalog_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    point_count: Mapped[int] = mapped_column(Integer, nullable=False)
    eligible_count: Mapped[int] = mapped_column(Integer, nullable=False)


class DecisionPointModel(UuidPkMixin, CreatedAtMixin, Base):
    __tablename__ = "decision_points"
    __table_args__ = (
        ForeignKeyConstraint(
            ["extraction_id"], ["decision_extractions.id"], name="fk_decision_points_extraction"
        ),
        # Identidade do estado de origem inteira (id + partida + participante +
        # evento + versão): as colunas copiadas aqui nunca divergem do estado.
        ForeignKeyConstraint(
            [
                "decision_state_id",
                "match_id",
                "participant_id",
                "event_source_index",
                "reconstruction_version",
            ],
            [
                "decision_states.id",
                "decision_states.match_id",
                "decision_states.participant_id",
                "decision_states.event_source_index",
                "decision_states.state_version",
            ],
            name="fk_decision_points_state_identity",
        ),
        ForeignKeyConstraint(
            ["catalog_id"], ["catalog_versions.id"], name="fk_decision_points_catalog"
        ),
        UniqueConstraint("extraction_id", "decision_state_id", name="uq_decision_points_state"),
        CheckConstraint(
            "(eligible AND features IS NOT NULL) OR (NOT eligible AND features IS NULL)",
            name="eligible_iff_features",
        ),
        CheckConstraint(
            "main_item_ordinal IS NULL OR main_item_ordinal >= 1", name="ordinal_positive"
        ),
        CheckConstraint("event_timestamp_ms >= 0", name="timestamp_non_negative"),
        # Paginação das rotas de leitura: (partida, participante, extração = tupla de
        # versões, tempo, source_index, id).
        Index(
            "ix_decision_points_pagination",
            "match_id",
            "participant_id",
            "extraction_id",
            "event_timestamp_ms",
            "event_source_index",
            "id",
        ),
    )

    extraction_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    decision_state_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    match_id: Mapped[str] = mapped_column(String, nullable=False)
    participant_id: Mapped[int] = mapped_column(Integer, nullable=False)
    event_source_index: Mapped[int] = mapped_column(Integer, nullable=False)
    event_timestamp_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    reconstruction_version: Mapped[int] = mapped_column(Integer, nullable=False)
    selection_version: Mapped[str] = mapped_column(String, nullable=False)
    feature_schema_version: Mapped[str] = mapped_column(String, nullable=False)
    scope_version: Mapped[str] = mapped_column(String, nullable=False)
    classification_version: Mapped[str | None] = mapped_column(String, nullable=True)
    curation_rules_version: Mapped[str | None] = mapped_column(String, nullable=True)
    catalog_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    target_item_id: Mapped[int] = mapped_column(Integer, nullable=False)
    target_class: Mapped[str | None] = mapped_column(String, nullable=True)
    main_item_ordinal: Mapped[int | None] = mapped_column(Integer, nullable=True)
    eligible: Mapped[bool] = mapped_column(Boolean, nullable=False)
    reasons: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    budget: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    # none_as_null: None grava SQL NULL (não o literal JSON `null`); a restrição
    # eligible_iff_features depende de `features IS NULL` para pontos inelegíveis.
    features: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
