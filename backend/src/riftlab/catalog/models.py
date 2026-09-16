from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String
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


class CatalogIdentityModel(Base):
    """Uma linha por identidade lógica (game_patch, data_dragon_version,
    mapping_version) — chave estável que existe (via upsert idempotente)
    ANTES de qualquer catalog_versions ser inserido para ela, ao contrário
    de um índice único sobre catalog_versions (revisão complementar
    CC-202: um índice único só impede duas linhas canônicas simultâneas
    DEPOIS de ambas tentarem existir — duas sincronizações concorrentes de
    conteúdo divergente para uma identidade nova, sem nenhuma linha
    catalog_versions ainda visível uma para a outra, colidiam só no COMMIT,
    e a perdedora recebia IntegrityError bruto, derrubando a transação
    inteira e perdendo o conteúdo que tinha acabado de inserir).

    canonical_catalog_id é atualizado sob um lock de linha
    (SELECT ... FOR UPDATE) desta própria linha de identidade — serializa
    de verdade toda promoção concorrente para a mesma identidade, nunca um
    erro de índice: a segunda transação simplesmente espera a primeira
    commitar, então prossegue e decide de forma determinística (última
    transação a COMMITAR depois de obter o lock vence — mesma semântica de
    "sync é uma ação deliberada", nunca uma corrida por ordem de chegada)."""

    __tablename__ = "catalog_identities"

    game_patch: Mapped[str] = mapped_column(String, primary_key=True)
    data_dragon_version: Mapped[str] = mapped_column(String, primary_key=True)
    mapping_version: Mapped[str] = mapped_column(String, primary_key=True)
    canonical_catalog_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("catalog_versions.id"), nullable=True
    )


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
