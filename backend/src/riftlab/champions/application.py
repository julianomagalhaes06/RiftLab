"""Sincronização e leitura do catálogo de campeões (CC-208).

Fronteiras:
- `ensure_champion_catalog` é o ÚNICO caminho com rede: só busca quando o patch tem
  entrada no mapping explícito (`configs/catalog-mappings.yaml`) e o catálogo daquela
  (versão, idioma) ainda não existe no banco. Patch sem mapping não chama a rede.
  Chamada pela preparação da partida (worker) e pelo `cc catalog sync-champions`.
- `champion_assets` só lê o banco (usada pelas rotas GET de partidas): nunca busca o
  CDN, então abrir uma partida não vira coleta implícita.
- Partida DEMO nunca recebe campeão real, qualquer que seja o id sintético."""

from __future__ import annotations

import enum
import logging
import uuid
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from riftlab.catalog.infrastructure.catalog_mapping import CatalogMapping
from riftlab.catalog.infrastructure.data_dragon_transport import (
    VERSION_PATTERN,
    RawDataDragonResponse,
)
from riftlab.champions.domain import IMAGE_FILE_PATTERN, parse_champion_catalog
from riftlab.champions.models import ChampionCatalogModel, ChampionModel
from riftlab.shared.api.common import MATCH_ID_DEMO_PATTERN
from riftlab.shared.db import read_only_unit_of_work, unit_of_work

logger = logging.getLogger(__name__)

SessionFactory = async_sessionmaker[AsyncSession]
ChampionFetcher = Callable[[str, str], Awaitable[RawDataDragonResponse]]

CHAMPION_LOCALE = "pt_BR"


class ChampionSyncOutcome(enum.StrEnum):
    NO_MAPPING = "NO_MAPPING"  # patch fora do mapping: nenhuma rede
    CACHED = "CACHED"  # catálogo já no banco: nenhuma rede
    SYNCED = "SYNCED"


@dataclass(frozen=True, slots=True)
class ChampionAsset:
    """O que a API divulga: nome e arquivo já validados + a versão fixada."""

    name: str
    data_dragon_version: str
    image_file: str


async def _catalog_id(session: AsyncSession, version: str, locale: str) -> uuid.UUID | None:
    return await session.scalar(
        select(ChampionCatalogModel.id).where(
            ChampionCatalogModel.data_dragon_version == version,
            ChampionCatalogModel.locale == locale,
        )
    )


async def ensure_champion_catalog(
    session_factory: SessionFactory,
    mapping: CatalogMapping,
    patch: str,
    fetch: ChampionFetcher,
    *,
    locale: str = CHAMPION_LOCALE,
) -> ChampionSyncOutcome:
    entry = mapping.resolve(patch)
    if entry is None:
        return ChampionSyncOutcome.NO_MAPPING
    version = entry.data_dragon_version
    async with read_only_unit_of_work(session_factory) as session:
        if await _catalog_id(session, version, locale) is not None:
            return ChampionSyncOutcome.CACHED
    raw = await fetch(version, locale)
    catalog = parse_champion_catalog(raw.body, expected_version=version, locale=locale)
    try:
        async with unit_of_work(session_factory) as session:
            row = ChampionCatalogModel(
                data_dragon_version=version,
                locale=locale,
                raw_sha256=raw.sha256,
                rejected_entries=catalog.rejected,
            )
            session.add(row)
            await session.flush()
            session.add_all(
                ChampionModel(
                    catalog_id=row.id,
                    champion_key=e.champion_key,
                    champion_text_id=e.champion_text_id,
                    name=e.name,
                    image_file=e.image_file,
                )
                for e in catalog.entries
            )
    except IntegrityError:
        # Sincronização concorrente gravou a mesma (versão, idioma) primeiro.
        return ChampionSyncOutcome.CACHED
    logger.info(
        "catálogo de campeões sincronizado",
        extra={"version": version, "entries": len(catalog.entries), "rejected": catalog.rejected},
    )
    return ChampionSyncOutcome.SYNCED


async def champion_assets(
    session: AsyncSession,
    mapping: CatalogMapping | None,
    match_id: str,
    patch: str,
    champion_ids: Iterable[int],
    *,
    locale: str = CHAMPION_LOCALE,
) -> dict[int, ChampionAsset]:
    """Só banco. {} para DEMO, patch sem mapping ou catálogo ainda não sincronizado."""
    if MATCH_ID_DEMO_PATTERN.fullmatch(match_id) or mapping is None:
        return {}
    entry = mapping.resolve(patch)
    if entry is None or not VERSION_PATTERN.fullmatch(entry.data_dragon_version):
        return {}
    catalog_id = await _catalog_id(session, entry.data_dragon_version, locale)
    ids = sorted(set(champion_ids))
    if catalog_id is None or not ids:
        return {}
    rows = await session.scalars(
        select(ChampionModel).where(
            ChampionModel.catalog_id == catalog_id, ChampionModel.champion_key.in_(ids)
        )
    )
    return {
        row.champion_key: ChampionAsset(row.name, entry.data_dragon_version, row.image_file)
        for row in rows
        if IMAGE_FILE_PATTERN.fullmatch(row.image_file)  # defesa em profundidade
    }
