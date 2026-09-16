from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from riftlab.catalog.domain import CatalogVersion, Item
from riftlab.catalog.models import CatalogVersionModel, ItemModel
from riftlab.shared.errors import ConflictError


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
        try:
            # flush (não commit) para que a violação do índice único de
            # identidade de conteúdo seja observada agora, dentro da
            # transação do chamador, e traduzida para um erro de domínio
            # (mesmo padrão de ingestion.create_import_job, CC-201).
            await self._session.flush()
        except IntegrityError as exc:
            raise ConflictError(
                f"já existe catálogo com sha256={catalog.sha256!r} (identidade de conteúdo)"
            ) from exc

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
        return _catalog_version_from_row(row) if row is not None else None

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

    async def find_catalog_version_by_content_hash(self, sha256: str) -> CatalogVersion | None:
        stmt = select(CatalogVersionModel).where(CatalogVersionModel.sha256 == sha256)
        result = await self._session.execute(stmt)
        row = result.scalars().first()
        return _catalog_version_from_row(row) if row is not None else None

    async def find_latest_catalog_version(
        self, game_patch: str, data_dragon_version: str, mapping_version: str
    ) -> CatalogVersion | None:
        stmt = (
            select(CatalogVersionModel)
            .where(
                CatalogVersionModel.game_patch == game_patch,
                CatalogVersionModel.data_dragon_version == data_dragon_version,
                CatalogVersionModel.mapping_version == mapping_version,
            )
            .order_by(CatalogVersionModel.created_at.desc())
            .limit(1)
        )
        result = await self._session.execute(stmt)
        row = result.scalars().first()
        return _catalog_version_from_row(row) if row is not None else None


def _catalog_version_from_row(row: CatalogVersionModel) -> CatalogVersion:
    return CatalogVersion(
        id=row.id,
        game_patch=row.game_patch,
        data_dragon_version=row.data_dragon_version,
        sha256=row.sha256,
        mapping_version=row.mapping_version,
    )
