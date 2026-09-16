from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, Integer, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from riftlab.shared.db import Base, CreatedAtMixin, UuidPkMixin


class CatalogVersionModel(UuidPkMixin, CreatedAtMixin, Base):
    __tablename__ = "catalog_versions"
    __table_args__ = (
        # Identidade de conteúdo (CC-202): raw+mapping+curadoria juntos.
        # Sync idempotente por construção — mesmo conteúdo nunca duplica
        # linha; conteúdo divergente sempre gera identidade (e linha) nova,
        # nunca sobrescreve a existente.
        Index("uq_catalog_versions_sha256", "sha256", unique=True),
        # No máximo uma linha por identidade (game_patch, data_dragon_version,
        # mapping_version) pode ser a canônica — revisão CC-202: substitui o
        # antigo ORDER BY created_at (empate possível, escolha incidental
        # por data). A associação canônica é deliberada, atualizada
        # explicitamente por mark_canonical a cada sync bem-sucedido.
        Index(
            "ux_catalog_versions_canonical_identity",
            "game_patch",
            "data_dragon_version",
            "mapping_version",
            unique=True,
            postgresql_where=text("is_canonical"),
        ),
    )

    game_patch: Mapped[str] = mapped_column(String, nullable=False)
    data_dragon_version: Mapped[str] = mapped_column(String, nullable=False)
    sha256: Mapped[str] = mapped_column(String, nullable=False)
    mapping_version: Mapped[str] = mapped_column(String, nullable=False)
    # Proveniência persistida separadamente do hash combinado (revisão
    # CC-202): sha256 não é reversível, sem estes dois campos não seria
    # possível recuperar qual fetch/curadoria gerou o catálogo.
    raw_hash: Mapped[str] = mapped_column(String, nullable=False)
    curation_rules_version: Mapped[str] = mapped_column(String, nullable=False)
    is_canonical: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class ItemModel(Base):
    __tablename__ = "items"
    __table_args__ = (CheckConstraint("price >= 0", name="price_non_negative"),)

    catalog_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("catalog_versions.id"), primary_key=True
    )
    item_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    price: Mapped[int] = mapped_column(Integer, nullable=False)
    recipe: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    flags: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    schema_version: Mapped[str] = mapped_column(String, nullable=False, default="v1")


class CatalogExcludedItemModel(Base):
    """Item presente na fonte bruta mas excluído da curadoria com motivo
    explícito (revisão CC-202) — sem esta tabela, um consumidor não
    distingue item inexistente de item conhecido e explicitamente não
    suportado ao ler um catálogo já persistido."""

    __tablename__ = "catalog_excluded_items"

    catalog_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("catalog_versions.id"), primary_key=True
    )
    item_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    reason_code: Mapped[str] = mapped_column(String, nullable=False)
    detail: Mapped[str] = mapped_column(String, nullable=False)
