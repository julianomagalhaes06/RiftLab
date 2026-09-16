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
from riftlab.catalog.domain import (
    CatalogItemSourceDTO,
    CatalogSourceResult,
    CatalogVersion,
    ExcludedCatalogItem,
)
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
        raw_hash="a" * 64,
        curation_rules_version="v1",
    )
    catalog_b = CatalogVersion(
        id=catalog_id_b,
        game_patch="16.17",
        data_dragon_version="16.17.1",
        sha256=content_hash,
        mapping_version="v1",
        raw_hash="a" * 64,
        curation_rules_version="v1",
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


async def test_sync_persists_raw_hash_and_curation_rules_version(session_factory) -> None:
    """Achado real da revisão: sync_catalog persistia só o hash combinado,
    nunca a proveniência (raw_hash/curation_rules_version) separadamente —
    irrecuperável depois, já que sha256 não é reversível."""
    raw_hash = uuid.uuid4().hex + uuid.uuid4().hex
    source = _FakeCatalogSource(_result(raw_hash=raw_hash, curation_rules_version="v7-teste"))

    catalog = await sync_catalog(session_factory, source, "16.17")

    assert catalog.raw_hash == raw_hash
    assert catalog.curation_rules_version == "v7-teste"

    async with unit_of_work(session_factory) as session:
        repo = SqlAlchemyCatalogRepository(session)
        reloaded = await repo.get_catalog_version(catalog.id)
    assert reloaded is not None
    assert reloaded.raw_hash == raw_hash
    assert reloaded.curation_rules_version == "v7-teste"


async def test_sync_persists_excluded_items_with_reason(session_factory) -> None:
    """Achado real da revisão: excluded_items existia no DTO mas nunca era
    persistido — sem isso, um consumidor futuro não distingue item
    inexistente de item conhecido e explicitamente não suportado."""
    raw_hash = uuid.uuid4().hex + uuid.uuid4().hex
    result_with_excluded = CatalogSourceResult(
        game_patch="16.17",
        origin=SourceOrigin.COMPLETED_MATCH,
        schema_version="test_v1",
        raw_hash=raw_hash,
        data_dragon_version="16.17.1",
        mapping_version="v1",
        curation_rules_version="v1",
        items=(CatalogItemSourceDTO(item_id=1001, price=400),),
        excluded_items=(
            ExcludedCatalogItem(
                item_id=9999,
                reason_code="SPECIAL_RECIPE_UNSUPPORTED",
                detail="item sintético com specialRecipe",
            ),
        ),
    )
    source = _FakeCatalogSource(result_with_excluded)

    catalog = await sync_catalog(session_factory, source, "16.17")

    async with unit_of_work(session_factory) as session:
        repo = SqlAlchemyCatalogRepository(session)
        excluded = await repo.list_excluded_items(catalog.id)
    assert len(excluded) == 1
    assert excluded[0].item_id == 9999
    assert excluded[0].reason_code == "SPECIAL_RECIPE_UNSUPPORTED"


async def test_resync_different_content_same_identity_switches_canonical(session_factory) -> None:
    """Achado real da revisão: resolução usava ORDER BY created_at (empate
    possível, escolha incidental por data). Duas variantes de conteúdo para
    a MESMA identidade (patch/dataDragon/mapping — ex.: correção de
    curadoria ou mudança real do raw) precisam de seleção determinística: a
    sincronização mais recente vira canônica explicitamente, a anterior
    continua consultável por ID exato, intacta, nunca sobrescrita."""
    mapping = parse_catalog_mapping_document(_MAPPING_1617_YAML)

    raw_hash_a = uuid.uuid4().hex + uuid.uuid4().hex
    source_a = _FakeCatalogSource(_result(raw_hash=raw_hash_a, price=400))
    catalog_a = await sync_catalog(session_factory, source_a, "16.17")
    resolved_after_a = await resolve_catalog_for_match(session_factory, mapping, "16.17")
    assert resolved_after_a is not None
    assert resolved_after_a.id == catalog_a.id

    raw_hash_b = uuid.uuid4().hex + uuid.uuid4().hex
    source_b = _FakeCatalogSource(_result(raw_hash=raw_hash_b, price=999))
    catalog_b = await sync_catalog(session_factory, source_b, "16.17")
    resolved_after_b = await resolve_catalog_for_match(session_factory, mapping, "16.17")
    assert resolved_after_b is not None
    assert resolved_after_b.id == catalog_b.id
    assert resolved_after_b.id != catalog_a.id  # canônico mudou de linha

    # Catálogo antigo permanece consultável por ID exato, com o preço
    # original intacto — nunca uma UPDATE retroativa de item referenciado.
    async with unit_of_work(session_factory) as session:
        repo = SqlAlchemyCatalogRepository(session)
        old_item = await repo.get_item(catalog_a.id, 1001)
    assert old_item is not None
    assert old_item.price == 400


async def test_concurrent_sync_catalog_service_calls_same_content_converge(
    session_factory,
) -> None:
    """Achado real da revisão: o teste de concorrência existente exercitava
    só add_catalog_version no nível do repositório. Este exercita o SERVIÇO
    sync_catalog completo (busca+valida+persiste+marca canônico) com duas
    chamadas reais e concorrentes de verdade — não sequencial: um
    asyncio.Event libera as duas ao mesmo tempo depois de ambas já estarem
    bloqueadas, forçando sobreposição real das transações. As duas
    precisam devolver a identidade completa (id/sha256/raw_hash/
    curation_rules_version) da mesma linha vencedora, sem exceção
    propagada nem persistência parcial."""
    raw_hash = uuid.uuid4().hex + uuid.uuid4().hex
    shared_result = _result(raw_hash=raw_hash, curation_rules_version="v-concorrencia")
    release = asyncio.Event()

    class _SlowFakeCatalogSource:
        def __init__(self, result: CatalogSourceResult) -> None:
            self._result = result

        async def load_catalog(self, game_patch: str) -> CatalogSourceResult:
            await asyncio.wait_for(release.wait(), timeout=5)
            return self._result

    async def _sync(source: _SlowFakeCatalogSource) -> CatalogVersion:
        return await sync_catalog(session_factory, source, "16.17")

    task_a = asyncio.ensure_future(_sync(_SlowFakeCatalogSource(shared_result)))
    task_b = asyncio.ensure_future(_sync(_SlowFakeCatalogSource(shared_result)))
    await asyncio.sleep(0.2)  # garante que as duas já estão bloqueadas em release.wait()
    release.set()

    catalog_a, catalog_b = await asyncio.wait_for(asyncio.gather(task_a, task_b), timeout=15)

    assert catalog_a.id == catalog_b.id
    expected_hash = compute_catalog_content_hash(shared_result)
    assert catalog_a.sha256 == catalog_b.sha256 == expected_hash
    assert catalog_a.raw_hash == catalog_b.raw_hash == raw_hash
    assert catalog_a.curation_rules_version == catalog_b.curation_rules_version == "v-concorrencia"

    # Conjunto completo de itens persistido de verdade, não só a identidade.
    async with unit_of_work(session_factory) as session:
        repo = SqlAlchemyCatalogRepository(session)
        item = await repo.get_item(catalog_a.id, 1001)
    assert item is not None
