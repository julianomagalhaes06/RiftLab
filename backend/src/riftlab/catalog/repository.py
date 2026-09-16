from __future__ import annotations

import uuid

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from riftlab.catalog.domain import CatalogVersion, ExcludedCatalogItem, Item
from riftlab.catalog.models import CatalogExcludedItemModel, CatalogVersionModel, ItemModel
from riftlab.shared.errors import ConflictError, NotFoundError


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
                raw_hash=catalog.raw_hash,
                curation_rules_version=catalog.curation_rules_version,
                is_canonical=False,  # mark_canonical() decide isso explicitamente depois
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

    async def add_excluded_item(self, catalog_id: uuid.UUID, excluded: ExcludedCatalogItem) -> None:
        self._session.add(
            CatalogExcludedItemModel(
                catalog_id=catalog_id,
                item_id=excluded.item_id,
                reason_code=excluded.reason_code,
                detail=excluded.detail,
            )
        )

    async def list_excluded_items(self, catalog_id: uuid.UUID) -> list[ExcludedCatalogItem]:
        stmt = select(CatalogExcludedItemModel).where(
            CatalogExcludedItemModel.catalog_id == catalog_id
        )
        result = await self._session.execute(stmt)
        return [
            ExcludedCatalogItem(item_id=row.item_id, reason_code=row.reason_code, detail=row.detail)
            for row in result.scalars()
        ]

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

    async def find_canonical_catalog_version(
        self, game_patch: str, data_dragon_version: str, mapping_version: str
    ) -> CatalogVersion | None:
        stmt = select(CatalogVersionModel).where(
            CatalogVersionModel.game_patch == game_patch,
            CatalogVersionModel.data_dragon_version == data_dragon_version,
            CatalogVersionModel.mapping_version == mapping_version,
            CatalogVersionModel.is_canonical.is_(True),
        )
        result = await self._session.execute(stmt)
        row = result.scalars().first()
        return _catalog_version_from_row(row) if row is not None else None

    async def mark_canonical(self, catalog_id: uuid.UUID) -> None:
        row = await self._session.get(CatalogVersionModel, catalog_id)
        if row is None:
            raise NotFoundError(f"catalog_version {catalog_id} não encontrado")
        # Desmarca qualquer OUTRA linha da mesma identidade primeiro — o
        # índice único parcial (revisão CC-202) não é deferido, então nunca
        # pode haver duas linhas simultaneamente canônicas nem por um
        # instante dentro desta transação; a ordem aqui garante isso.
        await self._session.execute(
            update(CatalogVersionModel)
            .where(
                CatalogVersionModel.game_patch == row.game_patch,
                CatalogVersionModel.data_dragon_version == row.data_dragon_version,
                CatalogVersionModel.mapping_version == row.mapping_version,
                CatalogVersionModel.id != catalog_id,
                CatalogVersionModel.is_canonical.is_(True),
            )
            .values(is_canonical=False)
        )
        row.is_canonical = True
        await self._session.flush()


def _catalog_version_from_row(row: CatalogVersionModel) -> CatalogVersion:
    return CatalogVersion(
        id=row.id,
        game_patch=row.game_patch,
        data_dragon_version=row.data_dragon_version,
        sha256=row.sha256,
        mapping_version=row.mapping_version,
        raw_hash=row.raw_hash,
        curation_rules_version=row.curation_rules_version,
        is_canonical=row.is_canonical,
    )
