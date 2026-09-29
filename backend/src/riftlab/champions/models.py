from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from riftlab.shared.db import Base, CreatedAtMixin, UuidPkMixin


class ChampionCatalogModel(UuidPkMixin, CreatedAtMixin, Base):
    """Um catálogo por (versão do Data Dragon, idioma) — cache durável e limitado
    pelas versões do mapping explícito. Imutável depois de gravado."""

    __tablename__ = "champion_catalogs"
    __table_args__ = (
        Index("uq_champion_catalogs_version_locale", "data_dragon_version", "locale", unique=True),
    )

    data_dragon_version: Mapped[str] = mapped_column(String, nullable=False)
    locale: Mapped[str] = mapped_column(String, nullable=False)
    raw_sha256: Mapped[str] = mapped_column(String, nullable=False)
    rejected_entries: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class ChampionModel(Base):
    __tablename__ = "champions"

    catalog_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("champion_catalogs.id"), primary_key=True
    )
    champion_key: Mapped[int] = mapped_column(Integer, primary_key=True)
    champion_text_id: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    image_file: Mapped[str] = mapped_column(String, nullable=False)
