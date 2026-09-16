"""sync_catalog/resolve_catalog_for_match (CC-202) contra PostgreSQL real:
idempotência sob concorrência real, identidade de conteúdo nunca sobrescreve
uma versão publicada, resolução determinística partida->catálogo."""

from __future__ import annotations

import asyncio
import uuid

import pytest

from riftlab.catalog.application import (
    compute_catalog_content_hash,
    resolve_catalog_for_match,
    sync_catalog,
)
from riftlab.catalog.domain import CatalogItemSourceDTO, CatalogSourceResult, CatalogVersion
from riftlab.catalog.infrastructure.catalog_mapping import parse_catalog_mapping_document
from riftlab.catalog.repository import SqlAlchemyCatalogRepository
from riftlab.shared.db import unit_of_work
from riftlab.shared.domain import SourceOrigin
from riftlab.shared.errors import ConflictError, SourceSchemaError

_MAPPING_1617_YAML = (
    b'schemaVersion: v1\nmappingVersion: v1\nmappings: {"16.17": {"dataDragonVersion": "16.17.1"}}'
)


class _FakeCatalogSource:
    """Implementação mínima do Protocol CatalogSource, sem I/O — devolve um
    CatalogSourceResult fixo por game_patch, controlado pelo teste (mesmo
    padrão de FakeCatalogSource em test_demo_catalog_source.py, CC-104)."""

    def __init__(self, result: CatalogSourceResult) -> None:
        self._result = result

    async def load_catalog(self, game_patch: str) -> CatalogSourceResult:
        return self._result


def _result(
    game_patch: str = "16.17",
    raw_hash: str = "a" * 64,
    price: int = 400,
    mapping_version: str = "v1",
    curation_rules_version: str = "v1",
) -> CatalogSourceResult:
    return CatalogSourceResult(
        game_patch=game_patch,
        origin=SourceOrigin.COMPLETED_MATCH,
        schema_version="test_v1",
        raw_hash=raw_hash,
        data_dragon_version="16.17.1",
        mapping_version=mapping_version,
        curation_rules_version=curation_rules_version,
        items=(CatalogItemSourceDTO(item_id=1001, price=price),),
    )


async def test_sync_same_content_twice_is_idempotent_same_identity(session_factory) -> None:
    raw_hash = uuid.uuid4().hex + uuid.uuid4().hex
    source = _FakeCatalogSource(_result(raw_hash=raw_hash))

    first = await sync_catalog(session_factory, source, "16.17")
    second = await sync_catalog(session_factory, source, "16.17")

    assert first.id == second.id
    assert first.sha256 == second.sha256


async def test_sync_different_content_creates_new_identity_old_row_intact(
    session_factory,
) -> None:
    raw_hash = uuid.uuid4().hex + uuid.uuid4().hex
    old_source = _FakeCatalogSource(_result(raw_hash=raw_hash, price=400))
    new_source = _FakeCatalogSource(_result(raw_hash=raw_hash + "-changed", price=999))

    old_catalog = await sync_catalog(session_factory, old_source, "16.17")
    new_catalog = await sync_catalog(session_factory, new_source, "16.17")

    assert old_catalog.id != new_catalog.id
    assert old_catalog.sha256 != new_catalog.sha256

    async with unit_of_work(session_factory) as session:
        repo = SqlAlchemyCatalogRepository(session)
        old_item = await repo.get_item(old_catalog.id, 1001)
    assert old_item is not None
    assert old_item.price == 400  # nunca sobrescrito pela sincronização nova


async def test_sync_rejects_recipe_cycle_nothing_persisted(session_factory) -> None:
    raw_hash = uuid.uuid4().hex + uuid.uuid4().hex
    cyclical = CatalogSourceResult(
        game_patch="16.17",
        origin=SourceOrigin.COMPLETED_MATCH,
        schema_version="test_v1",
        raw_hash=raw_hash,
        data_dragon_version="16.17.1",
        mapping_version="v1",
        curation_rules_version="v1",
        items=(CatalogItemSourceDTO(item_id=1, price=1, recipe=[{"itemId": 1, "quantity": 1}]),),
    )
    source = _FakeCatalogSource(cyclical)

    with pytest.raises(SourceSchemaError) as excinfo:
        await sync_catalog(session_factory, source, "16.17")
    assert excinfo.value.code == "CATALOG_RECIPE_CYCLE"

    content_hash = compute_catalog_content_hash(cyclical)
    async with unit_of_work(session_factory) as session:
        repo = SqlAlchemyCatalogRepository(session)
        stored = await repo.find_catalog_version_by_content_hash(content_hash)
    assert stored is None  # nenhuma linha parcial


async def test_concurrent_add_catalog_version_same_content_only_one_wins(
    session_factory,
) -> None:
    """Concorrência REAL (duas transações independentes, a segunda enviada
    ao Postgres enquanto a primeira ainda está aberta/não commitada — não
    sequencial), mesmo padrão de test_import_job_concurrency.py (CC-201):
    dois catálogos com a MESMA identidade de conteúdo (sha256) competem pelo
    índice único; a perdedora nunca recebe uma linha duplicada, recebe
    ConflictError."""
    catalog_id_a, catalog_id_b = uuid.uuid4(), uuid.uuid4()
    content_hash = uuid.uuid4().hex + uuid.uuid4().hex
    catalog_a = CatalogVersion(
        id=catalog_id_a,
        game_patch="16.17",
        data_dragon_version="16.17.1",
        sha256=content_hash,
        mapping_version="v1",
    )
    catalog_b = CatalogVersion(
        id=catalog_id_b,
        game_patch="16.17",
        data_dragon_version="16.17.1",
        sha256=content_hash,
        mapping_version="v1",
    )

    a_inserted = asyncio.Event()
    b_attempted = asyncio.Event()
    results: dict[str, str] = {}

    async def txn_a() -> None:
        async with session_factory() as session:
            repo = SqlAlchemyCatalogRepository(session)
            await repo.add_catalog_version(catalog_a)
            a_inserted.set()
            # Mantém a transação aberta (sem commit) até a tentativa
            # concorrente já ter sido enviada ao Postgres e estar bloqueada
            # no lock da linha ainda não commitada.
            await asyncio.wait_for(b_attempted.wait(), timeout=5)
            await session.commit()
        results["a"] = "committed"

    async def txn_b() -> None:
        await asyncio.wait_for(a_inserted.wait(), timeout=5)
        async with session_factory() as session:
            repo = SqlAlchemyCatalogRepository(session)
            insert_task = asyncio.ensure_future(repo.add_catalog_version(catalog_b))
            await asyncio.sleep(0.3)
            b_attempted.set()
            with pytest.raises(ConflictError):
                await asyncio.wait_for(insert_task, timeout=5)
        results["b"] = "conflict"

    await asyncio.wait_for(asyncio.gather(txn_a(), txn_b()), timeout=15)

    assert results == {"a": "committed", "b": "conflict"}

    async with unit_of_work(session_factory) as session:
        repo = SqlAlchemyCatalogRepository(session)
        stored = await repo.find_catalog_version_by_content_hash(content_hash)
    assert stored is not None and stored.id == catalog_id_a  # só a vencedora persistiu


async def test_resolve_catalog_for_match_missing_mapping_returns_none(session_factory) -> None:
    mapping = parse_catalog_mapping_document(_MAPPING_1617_YAML)

    resolved = await resolve_catalog_for_match(session_factory, mapping, "13.1")

    assert resolved is None  # MISSING_CATALOG: patch fora do mapping


async def test_resolve_catalog_for_match_never_synced_returns_none(session_factory) -> None:
    # Identidade dedicada (patch/versão nunca usados por nenhum outro teste
    # deste arquivo) — evita contaminação cruzada: se outro teste já
    # sincronizou "16.17"/"16.17.1"/"v1" antes deste rodar, find_latest
    # encontraria aquela linha e este teste falharia por motivo errado.
    mapping = parse_catalog_mapping_document(
        b"schemaVersion: v1\nmappingVersion: v1\n"
        b'mappings: {"99.99": {"dataDragonVersion": "99.99.1"}}'
    )

    resolved = await resolve_catalog_for_match(session_factory, mapping, "99.99")

    assert resolved is None  # coberto pelo mapping, mas nunca sincronizado


async def test_resolve_catalog_for_match_after_sync_finds_exact_catalog(session_factory) -> None:
    raw_hash = uuid.uuid4().hex + uuid.uuid4().hex
    source = _FakeCatalogSource(_result(raw_hash=raw_hash))
    synced = await sync_catalog(session_factory, source, "16.17")

    mapping = parse_catalog_mapping_document(_MAPPING_1617_YAML)
    resolved = await resolve_catalog_for_match(session_factory, mapping, "16.17")

    assert resolved is not None
    assert resolved.id == synced.id
