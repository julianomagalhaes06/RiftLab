"""reconstruct_match (CC-203) contra PostgreSQL real: orquestrador completo
— busca (banco + fonte local demo, sem rede) -> reduz (puro) -> persiste
(uma transação). Usa o catálogo demo real do projeto (DEMO-1, já reconciliado
com os cenários de partida demo — ver fixtures/catalogs/demo_1.json) e o
importador real do CC-201 (process_next_job) para partir de uma partida
genuinamente persistida, não de um Match construído à mão."""

from __future__ import annotations

import uuid

import pytest
from pipeline_helpers import (
    DEMO_1_MAPPING as _DEMO_1_MAPPING,
)
from pipeline_helpers import (
    import_demo_match as _import_demo_match,
)
from pipeline_helpers import (
    sync_demo_catalog as _sync_demo_catalog,
)

from riftlab.catalog.classification import classify_catalog
from riftlab.catalog.infrastructure.catalog_mapping import CatalogMapping
from riftlab.catalog.repository import SqlAlchemyCatalogRepository
from riftlab.decisions.domain import MatchContext, ParticipantView
from riftlab.decisions.scope import DEMO_SCOPE
from riftlab.decisions.selection import evaluate_decision
from riftlab.ingestion.infrastructure.demo_source import DemoMatchSource
from riftlab.ingestion.repository import SqlAlchemyIngestionRepository
from riftlab.shared.db import unit_of_work
from riftlab.shared.errors import NotFoundError
from riftlab.timeline.application import RECONSTRUCTION_ALGORITHM_VERSION, reconstruct_match
from riftlab.timeline.domain import DecisionState, TimelineReplayError
from riftlab.timeline.empirical_rules import EmpiricalRules, RuleEntry
from riftlab.timeline.repository import SqlAlchemyTimelineRepository


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
        marker = await SqlAlchemyTimelineRepository(session).get_match_reconstruction(
            "DEMO-0007", RECONSTRUCTION_ALGORITHM_VERSION
        )
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


class _SourceForbiddenIfCalled:
    """Fonte que lança se chamada — prova direta de que o caminho
    idempotente nunca reabre a fonte (fixture/raw), nem para listar
    participantes."""

    async def load_match(self, match_id: str):
        raise AssertionError(
            f"match_source.load_match({match_id!r}) chamado no caminho idempotente — "
            "resultado já persistido nunca deveria reabrir a fonte"
        )


@pytest.mark.parametrize("_demo_match", ["DEMO-0002"], indirect=True)
async def test_reconstruct_match_idempotent_path_never_reopens_source(
    session_factory, tmp_path, _demo_match
) -> None:
    match_id = "DEMO-0002"
    await _sync_demo_catalog(session_factory)
    await _import_demo_match(session_factory, tmp_path, match_id)

    first = await reconstruct_match(session_factory, _DEMO_1_MAPPING, DemoMatchSource(), match_id)
    assert first.decision_states

    # Segunda chamada: fonte que levanta AssertionError se load_match for
    # invocado — se o caminho idempotente reabrisse a fonte, este teste
    # falharia com o AssertionError da fonte, não com uma asserção nossa.
    second = await reconstruct_match(
        session_factory, _DEMO_1_MAPPING, _SourceForbiddenIfCalled(), match_id
    )
    assert sorted(str(s.id) for s in second.decision_states) == sorted(
        str(s.id) for s in first.decision_states
    )
    assert second.catalog_id == first.catalog_id


@pytest.mark.parametrize("_demo_match", ["DEMO-0004"], indirect=True)
async def test_reconstruct_match_preserves_pre_correction_decision_states(
    session_factory, tmp_path, _demo_match
) -> None:
    """Tratamento não destrutivo de legado: simula uma partida já
    reconstruída por uma versão ANTERIOR do algoritmo (state_version=1, sem
    marca alguma — a marca versionada só existe desde a versão 2) inserindo
    um DecisionState "legado" diretamente. A reconstrução nova nunca
    apaga/sobrescreve essa linha nem colide com ela (versão diferente),
    grava sua própria marca e seus próprios estados em
    RECONSTRUCTION_ALGORITHM_VERSION, e uma reexecução permanece
    idempotente."""
    match_id = "DEMO-0004"
    await _sync_demo_catalog(session_factory)
    await _import_demo_match(session_factory, tmp_path, match_id)

    legacy_state = DecisionState(
        id=uuid.uuid4(),
        match_id=match_id,
        participant_id=1,
        event_source_index=0,
        state_version=1,  # versão anterior do algoritmo, sem marca versionada
        snapshot={"inventory": {"items": {}, "trinketItemId": None}, "purchasedItemId": 100002},
        features={},
        quality={"uncertain": False, "reasons": [], "goldEstimate": {}},
        catalog_id=None,
    )
    async with unit_of_work(session_factory) as session:
        await SqlAlchemyTimelineRepository(session).add_decision_state(legacy_state)

    result = await reconstruct_match(session_factory, _DEMO_1_MAPPING, DemoMatchSource(), match_id)
    assert result.decision_states  # reconstrução nova aconteceu de verdade
    assert all(s.state_version == RECONSTRUCTION_ALGORITHM_VERSION for s in result.decision_states)

    async with unit_of_work(session_factory) as session:
        all_states = await SqlAlchemyTimelineRepository(session).list_decision_states(match_id, 1)
    legacy_still_present = [s for s in all_states if s.state_version == 1]
    assert len(legacy_still_present) == 1
    assert legacy_still_present[0].id == legacy_state.id  # linha legada intacta, nunca apagada

    # Reexecução permanece idempotente (marca da versão atual já existe).
    second = await reconstruct_match(session_factory, _DEMO_1_MAPPING, DemoMatchSource(), match_id)
    assert sorted(str(s.id) for s in second.decision_states) == sorted(
        str(s.id) for s in result.decision_states
    )


@pytest.mark.parametrize("_demo_match", ["DEMO-0004"], indirect=True)
async def test_reconstruct_match_preserves_previous_version_marker_and_states(
    session_factory, tmp_path, _demo_match
) -> None:
    """Versões anteriores COM marca (todas de 1 até a corrente - 1, inclusive
    a imediatamente anterior, ex.: v4 -> v5), sem limpeza: cada marca e cada
    estado antigo permanecem intactos, a versão corrente grava a sua própria
    marca/estados, e a consulta idempotente da versão corrente devolve só os
    estados da versão corrente."""
    previous_versions = list(range(1, RECONSTRUCTION_ALGORITHM_VERSION))
    assert previous_versions  # a versão corrente nunca é a primeira
    match_id = "DEMO-0004"
    await _sync_demo_catalog(session_factory)
    await _import_demo_match(session_factory, tmp_path, match_id)

    previous_states = {
        version: DecisionState(
            id=uuid.uuid4(),
            match_id=match_id,
            participant_id=1,
            event_source_index=0,
            state_version=version,
            snapshot={"inventory": {"items": {}, "trinketItemId": None}, "purchasedItemId": 100002},
            features={},
            quality={"uncertain": False, "reasons": [], "goldEstimate": {}},
            catalog_id=None,
        )
        for version in previous_versions
    }
    async with unit_of_work(session_factory) as session:
        repo = SqlAlchemyTimelineRepository(session)
        for version, state in previous_states.items():
            await repo.add_decision_state(state)
            await repo.add_match_reconstruction(match_id, version, None)

    # A marca de uma versão anterior nunca conta como "já feita" para a corrente.
    async with unit_of_work(session_factory) as session:
        repo = SqlAlchemyTimelineRepository(session)
        assert (
            await repo.get_match_reconstruction(match_id, RECONSTRUCTION_ALGORITHM_VERSION) is None
        )

    result = await reconstruct_match(session_factory, _DEMO_1_MAPPING, DemoMatchSource(), match_id)
    assert result.decision_states
    assert all(s.state_version == RECONSTRUCTION_ALGORITHM_VERSION for s in result.decision_states)

    async with unit_of_work(session_factory) as session:
        repo = SqlAlchemyTimelineRepository(session)
        current_marker = await repo.get_match_reconstruction(
            match_id, RECONSTRUCTION_ALGORITHM_VERSION
        )
        previous_markers = {
            version: await repo.get_match_reconstruction(match_id, version)
            for version in previous_versions
        }
        all_states = await repo.list_decision_states(match_id, 1)
    assert current_marker is not None  # marca corrente própria, sem colisão
    assert all(marker is not None for marker in previous_markers.values())  # nunca apagadas
    for version, state in previous_states.items():
        rows = [s for s in all_states if s.state_version == version]
        assert [s.id for s in rows] == [state.id]  # linha anterior intacta

    # Reexecução: caminho idempotente devolve só os estados da versão corrente.
    second = await reconstruct_match(session_factory, _DEMO_1_MAPPING, DemoMatchSource(), match_id)
    assert all(s.state_version == RECONSTRUCTION_ALGORITHM_VERSION for s in second.decision_states)
    assert sorted(str(s.id) for s in second.decision_states) == sorted(
        str(s.id) for s in result.decision_states
    )


@pytest.mark.parametrize("_demo_match", ["DEMO-0002"], indirect=True)
async def test_persisted_v5_state_round_trips_into_decision_evaluation(
    session_factory, tmp_path, _demo_match
) -> None:
    """O estado v5 gravado no PostgreSQL (JSONB) volta íntegro e alimenta o
    módulo de decisões com o mesmo resultado do caminho em memória: campos
    novos (eventTimestampMs, levelObservation, retrospectiveTarget)
    preservados, chaves numéricas do inventário como strings, sem reabrir a
    fonte."""
    match_id = "DEMO-0002"
    catalog_id = await _sync_demo_catalog(session_factory)
    await _import_demo_match(session_factory, tmp_path, match_id)
    result = await reconstruct_match(session_factory, _DEMO_1_MAPPING, DemoMatchSource(), match_id)

    async with unit_of_work(session_factory) as session:
        ingestion = SqlAlchemyIngestionRepository(session)
        catalog_repo = SqlAlchemyCatalogRepository(session)
        match = await ingestion.get_match(match_id)
        participants = await ingestion.list_participants(match_id)
        states = await SqlAlchemyTimelineRepository(session).list_decision_states(match_id, 1)
        row = await catalog_repo.get_catalog_version(catalog_id)
        items = {
            item_id: item
            for item_id in (100001, 100002, 100010)
            if (item := await catalog_repo.get_item(catalog_id, item_id)) is not None
        }
    assert match is not None and row is not None
    states = [s for s in states if s.state_version == RECONSTRUCTION_ALGORITHM_VERSION]
    assert sorted(str(s.id) for s in states) == sorted(
        str(s.id) for s in result.decision_states if s.participant_id == 1
    )
    for state in states:  # campos v5 sobreviveram ao JSONB
        assert isinstance(state.snapshot["eventTimestampMs"], int)
        assert state.quality["levelObservation"]["value"] == 1
        assert state.quality["retrospectiveTarget"]["status"] == "COMPLETED"

    table = classify_catalog(items, curation_rules_version=row.curation_rules_version)
    points = [
        point
        for state in states
        if (
            point := evaluate_decision(
                state,
                participants=tuple(ParticipantView.from_participant(p) for p in participants),
                match=MatchContext.from_match(match),
                catalog=row,
                classification=table,
                scope=DEMO_SCOPE,
            )
        )
        is not None
    ]
    assert [p.target_item_id for p in points] == [100010]
    [point] = points
    assert point.eligible and point.main_item_ordinal == 1
    assert point.budget.codes == ("STALE_FRAME",) and point.budget.value == 500
    assert point.features is not None and point.features.values["level"] == 1


def _supported(rule_id: str, patches: frozenset[str]) -> RuleEntry:
    return RuleEntry(
        rule_id,
        1,
        f"regra {rule_id} (teste)",
        "EMPIRICALLY_SUPPORTED",
        patches,
        0.0,
        0,
        "evidencia-sintetica-de-teste",
        RECONSTRUCTION_ALGORITHM_VERSION,
    )


@pytest.mark.parametrize("_demo_match", ["DEMO-0002"], indirect=True)
async def test_reconstruct_match_records_provenance_only_for_rules_enabled_in_the_patch(
    session_factory, tmp_path, _demo_match
) -> None:
    """Regras ligadas para o patch da partida (DEMO-1) entram em `quality.empiricalRules` de cada
    estado, persistido; regras de OUTRO patch não entram (fora dele o comportamento é o da v6)."""
    await _sync_demo_catalog(session_factory)
    await _import_demo_match(session_factory, tmp_path, "DEMO-0002")
    rules = EmpiricalRules(
        entries={
            "R5": _supported("R5", frozenset({"DEMO-1"})),
            "R1": _supported("R1", frozenset({"16.17"})),
        }
    )

    result = await reconstruct_match(
        session_factory, _DEMO_1_MAPPING, DemoMatchSource(), "DEMO-0002", empirical_rules=rules
    )

    assert result.decision_states
    for state in result.decision_states:
        assert state.state_version == RECONSTRUCTION_ALGORITHM_VERSION
        assert [rule["ruleId"] for rule in state.quality["empiricalRules"]] == ["R5"]
        assert state.quality["empiricalRules"][0]["patchesValidated"] == ["DEMO-1"]
        assert state.quality["empiricalRules"][0]["evidence"] == "evidencia-sintetica-de-teste"
    async with unit_of_work(session_factory) as session:
        persisted = await SqlAlchemyTimelineRepository(session).list_decision_states("DEMO-0002", 1)
    assert persisted and all(
        [rule["ruleId"] for rule in s.quality["empiricalRules"]] == ["R5"] for s in persisted
    )


@pytest.mark.parametrize("_demo_match", ["DEMO-0001"], indirect=True)
async def test_reconstruct_match_without_a_registry_records_no_rules(
    session_factory, tmp_path, _demo_match
) -> None:
    await _sync_demo_catalog(session_factory)
    await _import_demo_match(session_factory, tmp_path, "DEMO-0001")

    result = await reconstruct_match(
        session_factory, _DEMO_1_MAPPING, DemoMatchSource(), "DEMO-0001"
    )

    assert result.decision_states
    assert all(state.quality["empiricalRules"] == [] for state in result.decision_states)
