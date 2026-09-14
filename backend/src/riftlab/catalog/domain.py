from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

from riftlab.shared.domain import SourceOrigin

CURRENT_ITEM_SCHEMA_VERSION = "v1"


@dataclass(frozen=True, slots=True)
class CatalogVersion:
    """Catálogo imutável de um patch. Uma nova versão nunca sobrescreve uma
    existente; correções viram uma nova linha, conforme domain-model.md."""

    id: uuid.UUID
    game_patch: str
    data_dragon_version: str
    sha256: str
    mapping_version: str


@dataclass(frozen=True, slots=True)
class Item:
    """Item só tem sentido dentro do catálogo (catalog_id, item_id). O mesmo
    item_id pode existir em catálogos distintos com dados diferentes."""

    catalog_id: uuid.UUID
    item_id: int
    price: int
    recipe: list[dict[str, Any]] = field(default_factory=list)
    attributes: dict[str, Any] = field(default_factory=dict)
    flags: dict[str, Any] = field(default_factory=dict)
    schema_version: str = CURRENT_ITEM_SCHEMA_VERSION


class CatalogRepository(Protocol):
    async def add_catalog_version(self, catalog: CatalogVersion) -> None: ...

    async def add_item(self, item: Item) -> None: ...

    async def get_catalog_version(self, catalog_id: uuid.UUID) -> CatalogVersion | None: ...

    async def get_item(self, catalog_id: uuid.UUID, item_id: int) -> Item | None: ...


@dataclass(frozen=True, slots=True)
class CatalogItemSourceDTO:
    """Item como a fonte bruta descreve, anterior à persistência como Item (que
    exige catalog_id já atribuído)."""

    item_id: int
    price: int
    recipe: list[dict[str, Any]] = field(default_factory=list)
    attributes: dict[str, Any] = field(default_factory=dict)
    flags: dict[str, Any] = field(default_factory=dict)
    schema_version: str = CURRENT_ITEM_SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class CatalogSourceResult:
    """Retorno bruto de CatalogSource, anterior a qualquer persistência como
    CatalogVersion/Item (CC-105/CC-202). origin=CURATED_DEMO é a única origem
    implementada nesta etapa."""

    game_patch: str
    origin: SourceOrigin
    schema_version: str
    raw_hash: str
    data_dragon_version: str
    mapping_version: str
    items: tuple[CatalogItemSourceDTO, ...]


class CatalogSource(Protocol):
    """Fonte plugável de catálogo. Consumidor depende só do Protocol; nenhuma URL
    ou caminho arbitrário vindo de usuário chega até uma implementação real."""

    async def load_catalog(self, game_patch: str) -> CatalogSourceResult:
        """Levanta SourceNotFoundError para game_patch fora do escopo da fonte e
        SourceSchemaError para JSON inválido, schema desconhecido ou item/campo
        estrutural obrigatório ausente."""
        ...
