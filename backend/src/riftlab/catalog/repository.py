from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from riftlab.catalog.domain import CatalogVersion, Item
from riftlab.catalog.models import CatalogVersionModel, ItemModel


class SqlAlchemyCatalogRepository:
    """Implementa CatalogRepository. Não faz commit: a UoW chamadora decide o
    limite da transação."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_catalog_version(self, catalog: CatalogVersion) -> None:
        self._session.add(
            CatalogVersionModel(
                id=catalog.id,
                game_patch=catalog.game_patch,
                data_dragon_version=catalog.data_dragon_version,
                sha256=catalog.sha256,
                mapping_version=catalog.mapping_version,
            )
        )

    async def add_item(self, item: Item) -> None:
        self._session.add(
            ItemModel(
                catalog_id=item.catalog_id,
                item_id=item.item_id,
                price=item.price,
                recipe=item.recipe,
                attributes=item.attributes,
                flags=item.flags,
                schema_version=item.schema_version,
            )
        )

    async def get_catalog_version(self, catalog_id: uuid.UUID) -> CatalogVersion | None:
        row = await self._session.get(CatalogVersionModel, catalog_id)
        if row is None:
            return None
        return CatalogVersion(
            id=row.id,
            game_patch=row.game_patch,
            data_dragon_version=row.data_dragon_version,
            sha256=row.sha256,
            mapping_version=row.mapping_version,
        )

    async def get_item(self, catalog_id: uuid.UUID, item_id: int) -> Item | None:
        row = await self._session.get(ItemModel, {"catalog_id": catalog_id, "item_id": item_id})
        if row is None:
            return None
        return Item(
            catalog_id=row.catalog_id,
            item_id=row.item_id,
            price=row.price,
            recipe=row.recipe,
            attributes=row.attributes,
            flags=row.flags,
            schema_version=row.schema_version,
        )
