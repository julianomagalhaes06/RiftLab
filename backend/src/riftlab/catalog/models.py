from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from riftlab.shared.db import Base, CreatedAtMixin, UuidPkMixin


class CatalogVersionModel(UuidPkMixin, CreatedAtMixin, Base):
    __tablename__ = "catalog_versions"

    game_patch: Mapped[str] = mapped_column(String, nullable=False)
    data_dragon_version: Mapped[str] = mapped_column(String, nullable=False)
    sha256: Mapped[str] = mapped_column(String, nullable=False)
    mapping_version: Mapped[str] = mapped_column(String, nullable=False)


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
