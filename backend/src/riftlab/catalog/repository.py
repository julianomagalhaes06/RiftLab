from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from riftlab.catalog.domain import CatalogVersion, ExcludedCatalogItem, Item
from riftlab.catalog.models import (
    CatalogExcludedItemModel,
    CatalogIdentityModel,
    CatalogVersionModel,
    ItemModel,
)
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

    async def list_items(self, catalog_id: uuid.UUID) -> list[Item]:
        stmt = (
            select(ItemModel).where(ItemModel.catalog_id == catalog_id).order_by(ItemModel.item_id)
        )
        result = await self._session.execute(stmt)
        return [
            Item(
                catalog_id=row.catalog_id,
                item_id=row.item_id,
                price=row.price,
                recipe=row.recipe,
                attributes=row.attributes,
                flags=row.flags,
                schema_version=row.schema_version,
            )
            for row in result.scalars()
        ]

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
        identity = await self._session.get(
            CatalogIdentityModel,
            {
                "game_patch": game_patch,
                "data_dragon_version": data_dragon_version,
                "mapping_version": mapping_version,
            },
        )
        if identity is None or identity.canonical_catalog_id is None:
            return None
        return await self.get_catalog_version(identity.canonical_catalog_id)

    async def mark_canonical(self, catalog_id: uuid.UUID) -> None:
        row = await self._session.get(CatalogVersionModel, catalog_id)
        if row is None:
            raise NotFoundError(f"catalog_version {catalog_id} não encontrado")

        # Upsert idempotente da linha de identidade — existe (via
        # ON CONFLICT DO NOTHING, nunca levanta se outra transação
        # concorrente já criou a mesma linha) mesmo antes de qualquer
        # catalog_versions existir para ela (revisão complementar CC-202:
        # sem isto, duas sincronizações concorrentes de uma identidade nova
        # não tinham nenhuma linha estável para travar).
        await self._session.execute(
            pg_insert(CatalogIdentityModel)
            .values(
                game_patch=row.game_patch,
                data_dragon_version=row.data_dragon_version,
                mapping_version=row.mapping_version,
                canonical_catalog_id=None,
            )
            .on_conflict_do_nothing(
                index_elements=["game_patch", "data_dragon_version", "mapping_version"]
            )
        )

        # SELECT ... FOR UPDATE trava a linha de identidade — serializa de
        # verdade toda promoção concorrente para a mesma identidade. A
        # segunda transação concorrente bloqueia aqui até a primeira
        # commitar (nunca um erro de índice bruto), então prossegue e
        # decide deterministicamente: última transação a commitar depois de
        # obter o lock vence, sem perder o conteúdo de nenhuma das duas
        # sincronizações (catalog_versions já foi inserido antes, em
        # nenhum momento é revertido por causa desta promoção).
        stmt = (
            select(CatalogIdentityModel)
            .where(
                CatalogIdentityModel.game_patch == row.game_patch,
                CatalogIdentityModel.data_dragon_version == row.data_dragon_version,
                CatalogIdentityModel.mapping_version == row.mapping_version,
            )
            .with_for_update()
        )
        result = await self._session.execute(stmt)
        identity = result.scalar_one()
        identity.canonical_catalog_id = catalog_id
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
    )
