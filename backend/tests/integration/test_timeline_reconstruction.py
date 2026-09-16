"""reconstruct_match (CC-203) contra PostgreSQL real: orquestrador completo
— busca (banco + fonte local demo, sem rede) -> reduz (puro) -> persiste
(uma transação). Usa o catálogo demo real do projeto (DEMO-1, já reconciliado
com os cenários de partida demo — ver fixtures/catalogs/demo_1.json) e o
importador real do CC-201 (process_next_job) para partir de uma partida
genuinamente persistida, não de um Match construído à mão."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import delete

from riftlab.catalog.application import sync_catalog
from riftlab.catalog.infrastructure.catalog_mapping import CatalogMapping
from riftlab.catalog.infrastructure.demo_source import DemoCatalogSource, demo_catalog_mapping
from riftlab.config import Settings
from riftlab.ingestion.application import process_next_job
from riftlab.ingestion.domain import ImportJob, ImportJobState, ImportMode
from riftlab.ingestion.infrastructure.demo_source import DemoMatchSource
from riftlab.ingestion.infrastructure.raw_store import DurableRawMatchStore
from riftlab.ingestion.models import ImportJobModel, MatchModel, ParticipantModel
from riftlab.ingestion.repository import SqlAlchemyIngestionRepository
from riftlab.shared.db import unit_of_work
from riftlab.shared.errors import NotFoundError
from riftlab.timeline.application import reconstruct_match
from riftlab.timeline.domain import TimelineReplayError
from riftlab.timeline.models import DecisionStateModel, MatchReconstructionModel, TimelineEventModel
from riftlab.timeline.repository import SqlAlchemyTimelineRepository


async def _reset_match(session_factory, match_id: str) -> None:
    """Achado real de higiene (mesma lição de test_worker_processing.py,
    CC-201): DEMO-0001/0002/0003 são identidades fixas COMPARTILHADAS com
    test_import_routes.py/test_worker_processing.py neste mesmo processo
    pytest. Uma partida deixada SUCCEEDED por este arquivo bloqueia
    permanentemente a criação de job desses arquivos-irmãos (índice único de
    import_jobs cobre SUCCEEDED desde a revisão do CC-201) — limpa a árvore
    completa da partida (marca de reconstrução, decisões, eventos,
    participantes, match, job) na ordem que respeita as FKs."""
    async with unit_of_work(session_factory) as session:
        await session.execute(
            delete(MatchReconstructionModel).where(MatchReconstructionModel.match_id == match_id)
        )
        await session.execute(
            delete(DecisionStateModel).where(DecisionStateModel.match_id == match_id)
        )
        await session.execute(
            delete(TimelineEventModel).where(TimelineEventModel.match_id == match_id)
        )
        await session.execute(
            delete(ImportJobModel).where(ImportJobModel.requested_match_id == match_id)
        )
        await session.execute(delete(ParticipantModel).where(ParticipantModel.match_id == match_id))
        await session.execute(delete(MatchModel).where(MatchModel.match_id == match_id))


@pytest_asyncio.fixture
async def _demo_match(session_factory, request) -> AsyncIterator[str]:
    """Reserva uma identidade DEMO-000X para o teste: limpa antes (recupera
    de uma rodada anterior interrompida) e SEMPRE depois, mesmo em falha de
    asserção (finally) — nunca deixa resíduo SUCCEEDED para os arquivos-
    irmãos que reusam a mesma identidade fixa dentro do mesmo processo
    pytest."""
    match_id: str = request.param
    await _reset_match(session_factory, match_id)
    try:
        yield match_id
    finally:
        await _reset_match(session_factory, match_id)


_FAKE_SUBJECT_HASH_SECRET = "segredo-local-de-teste-nao-real"  # noqa: S105

# DEMO-1 é o gamePatch real usado pelas fixtures demo de partida (CC-104) e
# pelo catálogo demo real (CC-202, fixtures/catalogs/demo_1.json) — os dois
# já reconciliados: item 100010 = 2x100001 + 1x100002, exatamente o cenário
# de demo_0002_receita_componentes.json. Derivado da própria fixture (mesmo
# helper usado pela CLI), nunca um literal solto duplicado aqui.
_DEMO_1_MAPPING: CatalogMapping = demo_catalog_mapping()


async def _sync_demo_catalog(session_factory) -> uuid.UUID:
    version = await sync_catalog(session_factory, DemoCatalogSource(), "DEMO-1")
    return version.id


async def _import_demo_match(session_factory, tmp_path, requested_match_id: str) -> None:
    async with unit_of_work(session_factory) as session:
        repo = SqlAlchemyIngestionRepository(session)
        job = ImportJob(
            id=uuid.uuid4(),
            requested_match_id=requested_match_id,
            mode=ImportMode.DEMO,
            state=ImportJobState.QUEUED,
        )
        await repo.create_import_job(job)

    settings = Settings(subject_hash_secret=_FAKE_SUBJECT_HASH_SECRET)
    raw_store = DurableRawMatchStore(tmp_path / "raw")
    processed = await process_next_job(session_factory, raw_store, settings)
    assert processed is True


@pytest.mark.parametrize("_demo_match", ["DEMO-0002"], indirect=True)
async def test_reconstruct_match_persists_states_matching_recipe_consumption(
    session_factory, tmp_path, _demo_match
) -> None:
    catalog_id = await _sync_demo_catalog(session_factory)
    await _import_demo_match(session_factory, tmp_path, "DEMO-0002")

    result = await reconstruct_match(
        session_factory, _DEMO_1_MAPPING, DemoMatchSource(), "DEMO-0002"
    )

    assert result.catalog_id == catalog_id
    by_item = {
        s.snapshot["purchasedItemId"]: s for s in result.decision_states if s.participant_id == 1
    }
    assert 100010 in by_item
    composite_snapshot = by_item[100010].snapshot["inventory"]
    assert composite_snapshot["items"]["100001"] == 2
    assert composite_snapshot["items"]["100002"] == 1
    assert by_item[100010].quality["uncertain"] is False
    assert by_item[100010].catalog_id == catalog_id

    async with unit_of_work(session_factory) as session:
        persisted = await SqlAlchemyTimelineRepository(session).list_decision_states("DEMO-0002", 1)
    # fixture tem 4 compras (100001 x2, 100002, 100010) — cada uma sua própria
    # decisão, mesmo duas delas sendo o mesmo item_id (nunca deduplicadas)
    assert len(persisted) == 4


@pytest.mark.parametrize("_demo_match", ["DEMO-0001"], indirect=True)
async def test_reconstruct_match_is_idempotent_never_duplicates(
    session_factory, tmp_path, _demo_match
) -> None:
    catalog_id = await _sync_demo_catalog(session_factory)
    await _import_demo_match(session_factory, tmp_path, "DEMO-0001")

    first = await reconstruct_match(
        session_factory, _DEMO_1_MAPPING, DemoMatchSource(), "DEMO-0001"
    )
    second = await reconstruct_match(
        session_factory, _DEMO_1_MAPPING, DemoMatchSource(), "DEMO-0001"
    )

    first_ids = sorted(str(s.id) for s in first.decision_states)
    second_ids = sorted(str(s.id) for s in second.decision_states)
    assert (
        first_ids == second_ids
    )  # segunda chamada devolve exatamente os mesmos estados, nunca duplica
    # Achado 5 da revisão: a segunda chamada devolvia um catalog_id RERESOLVIDO
    # na hora, podendo divergir do catalog_id realmente usado nos estados já
    # persistidos — agora vem da marca gravada junto da primeira execução.
    assert first.catalog_id == catalog_id
    assert second.catalog_id == catalog_id

    async with unit_of_work(session_factory) as session:
        persisted = await SqlAlchemyTimelineRepository(session).list_decision_states("DEMO-0001", 1)
        # participante 6 nunca compra nada em DEMO-0001 (fixture real do
        # projeto) — prova que idempotência não depende de qual participante
        # tem compra: zero estados para ele nas duas chamadas, sem erro.
        empty_participant = await SqlAlchemyTimelineRepository(session).list_decision_states(
            "DEMO-0001", 6
        )
    assert len(persisted) == len(first.decision_states)
    assert empty_participant == []


@pytest.mark.parametrize("_demo_match", ["DEMO-0007"], indirect=True)
async def test_reconstruct_match_idempotent_marker_independent_of_purchase_count(
    session_factory, tmp_path, _demo_match
) -> None:
    """Achado 5 da revisão: a checagem de idempotência antiga olhava só
    `list_decision_states` do primeiro participante — se esse participante
    especificamente não tivesse compra alguma (mas outro tivesse), a
    reexecução tentava reinserir estados já existentes e levantava
    IntegrityError. A marca explícita (`match_reconstructions`) nunca
    depende de qual participante comprou o quê (as fixtures demo reais
    deste projeto sempre têm o participante 1 primeiro com compra — a marca
    é a correção estrutural que nunca mais lê essa ordem para decidir
    idempotência, o que este teste confirma diretamente via o repositório)."""
    await _sync_demo_catalog(session_factory)
    await _import_demo_match(session_factory, tmp_path, "DEMO-0007")

    first = await reconstruct_match(
        session_factory, _DEMO_1_MAPPING, DemoMatchSource(), "DEMO-0007"
    )
    async with unit_of_work(session_factory) as session:
        marker = await SqlAlchemyTimelineRepository(session).get_match_reconstruction("DEMO-0007")
    assert marker is not None
    assert marker.catalog_id == first.catalog_id

    second = await reconstruct_match(
        session_factory, _DEMO_1_MAPPING, DemoMatchSource(), "DEMO-0007"
    )
    assert sorted(str(s.id) for s in second.decision_states) == sorted(
        str(s.id) for s in first.decision_states
    )


@pytest.mark.parametrize("_demo_match", ["DEMO-0005"], indirect=True)
async def test_reconstruct_match_rejects_source_diverging_from_persisted_raw_hash(
    session_factory, tmp_path, _demo_match
) -> None:
    """Achado 4 da revisão: nada garantia que a releitura da fonte local
    (frames/participantes) correspondia de verdade à mesma partida já
    persistida — nunca mistura uma releitura divergente com eventos já
    persistidos."""
    await _sync_demo_catalog(session_factory)
    await _import_demo_match(session_factory, tmp_path, "DEMO-0005")

    class _TamperedSource:
        def __init__(self, inner: DemoMatchSource) -> None:
            self._inner = inner

        async def load_match(self, match_id: str):
            import dataclasses

            result = await self._inner.load_match(match_id)
            return dataclasses.replace(result, raw_hash="0" * 64)

    with pytest.raises(TimelineReplayError) as excinfo:
        await reconstruct_match(
            session_factory, _DEMO_1_MAPPING, _TamperedSource(DemoMatchSource()), "DEMO-0005"
        )
    assert excinfo.value.code == "SOURCE_RAW_HASH_MISMATCH"


async def test_reconstruct_match_raises_not_found_for_never_imported_match(session_factory) -> None:
    with pytest.raises(NotFoundError):
        await reconstruct_match(
            session_factory, _DEMO_1_MAPPING, DemoMatchSource(), "DEMO-NEVER-IMPORTED"
        )


@pytest.mark.parametrize("_demo_match", ["DEMO-0003"], indirect=True)
async def test_reconstruct_match_without_catalog_mapping_persists_uncertain_states(
    session_factory, tmp_path, _demo_match
) -> None:
    await _import_demo_match(session_factory, tmp_path, "DEMO-0003")
    empty_mapping = CatalogMapping(schema_version="v1", mapping_version="v1", entries={})

    result = await reconstruct_match(session_factory, empty_mapping, DemoMatchSource(), "DEMO-0003")

    assert result.catalog_id is None
    assert result.decision_states  # ainda produz estados, nunca uma lista vazia por omissão
    assert all(s.catalog_id is None for s in result.decision_states)
    assert all(s.quality["uncertain"] is True for s in result.decision_states)
    assert all(
        any(r["code"] == "NO_CATALOG" for r in s.quality["reasons"]) for s in result.decision_states
    )
