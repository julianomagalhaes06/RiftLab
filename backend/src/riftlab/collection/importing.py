"""Importação do que foi COLETADO, a partir do raw local (CC-206): enfileira um job
de importação por partida coletada (idempotente: `request_import`) e, se pedido, drena
a fila em processo com `local_only=True`, ou seja, sem nunca buscar de novo. Cada
importação bem-sucedida enfileira a preparação automática do CC-205 na mesma transação,
então a partida só fica READY depois de reconstruída e extraída."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from riftlab.config import Settings
from riftlab.ingestion.application import process_next_job, raw_pair_available, request_import
from riftlab.ingestion.domain import ImportJobState, ImportMode
from riftlab.ingestion.infrastructure.raw_store import DurableRawMatchStore
from riftlab.ingestion.repository import SqlAlchemyIngestionRepository
from riftlab.preparation.application import CatalogSourceFactory, process_next_preparation
from riftlab.shared.db import unit_of_work

SessionFactory = async_sessionmaker[AsyncSession]
MAX_DRAIN_CYCLES = 100_000


@dataclass(frozen=True, slots=True)
class ImportEnqueueReport:
    enqueued: int
    already_requested: int
    raw_incomplete: int  # par match+timeline ausente/corrompido: nunca vira job


@dataclass(frozen=True, slots=True)
class DrainReport:
    import_cycles: int
    preparation_cycles: int


async def enqueue_collected_imports(
    session_factory: SessionFactory, raw_store: DurableRawMatchStore, match_ids: Iterable[str]
) -> ImportEnqueueReport:
    enqueued = already = incomplete = 0
    for match_id in match_ids:
        if not raw_pair_available(raw_store, match_id):
            incomplete += 1
            continue
        async with unit_of_work(session_factory) as session:
            latest = await SqlAlchemyIngestionRepository(session).get_latest_import_job(
                match_id, ImportMode.RIOT
            )
        if latest is not None and latest.state != ImportJobState.FAILED:
            already += 1
            continue
        await request_import(session_factory, match_id, ImportMode.RIOT)
        enqueued += 1
    return ImportEnqueueReport(enqueued, already, incomplete)


async def drain_local(
    session_factory: SessionFactory,
    raw_store: DurableRawMatchStore,
    settings: Settings,
    *,
    catalog_source_factory: CatalogSourceFactory | None = None,
) -> DrainReport:
    """Processa importações (só do raw local) e preparações até a fila esvaziar. Jobs em
    espera de nova tentativa (RETRY_WAIT no futuro) não são aguardados aqui."""
    imports = preparations = 0
    for _ in range(MAX_DRAIN_CYCLES):
        imported = await process_next_job(session_factory, raw_store, settings, local_only=True)
        prepared = await process_next_preparation(
            session_factory, raw_store, settings, catalog_source_factory
        )
        imports += int(imported)
        preparations += int(prepared)
        if not (imported or prepared):
            break
    return DrainReport(imports, preparations)
