"""reconstruct_match (CC-203) contra PostgreSQL real: orquestrador completo
— busca (banco + fonte local demo, sem rede) -> reduz (puro) -> persiste
(uma transação). Usa o catálogo demo real do projeto (DEMO-1, já reconciliado
com os cenários de partida demo — ver fixtures/catalogs/demo_1.json) e o
importador real do CC-201 (process_next_job) para partir de uma partida
genuinamente persistida, não de um Match construído à mão."""

from __future__ import annotations

import contextlib
import uuid
from dataclasses import replace

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
from sqlalchemy import select

from riftlab.catalog.application import sync_catalog
from riftlab.catalog.classification import classify_catalog
from riftlab.catalog.infrastructure.catalog_mapping import CatalogMapping, MappingEntry
from riftlab.catalog.infrastructure.demo_source import DemoCatalogSource
from riftlab.catalog.repository import SqlAlchemyCatalogRepository
from riftlab.decisions.domain import MatchContext, ParticipantView
from riftlab.decisions.scope import DEMO_SCOPE
from riftlab.decisions.selection import evaluate_decision
from riftlab.ingestion.infrastructure.demo_source import DemoMatchSource
from riftlab.ingestion.infrastructure.raw_store import DurableRawMatchStore
from riftlab.ingestion.models import MatchModel
from riftlab.ingestion.repository import SqlAlchemyIngestionRepository
from riftlab.shared.db import unit_of_work
from riftlab.shared.errors import NotFoundError
from riftlab.timeline.application import (
    CARRY_FORWARD_FROM_VERSION,
    RECONSTRUCTION_ALGORITHM_VERSION,
    reconstruct_match,
)
from riftlab.timeline.domain import DecisionState, TimelineReplayError
from riftlab.timeline.empirical_rules import DomainEntry, EmpiricalRules, RuleEntry
from riftlab.timeline.legacy_provenance import legacy_v10_context
from riftlab.timeline.models import DecisionStateModel
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


def _legacy_v10_rules() -> EmpiricalRules:
    context = legacy_v10_context()
    entries = {
        row["ruleId"]: RuleEntry(
            rule_id=row["ruleId"],
            rule_version=row["ruleVersion"],
            description="regra histórica do manifesto v10",
            status=row["status"],
            patches_validated=frozenset(row["patchesValidated"]),
            contra_rate=row["contraRate"],
            counter_examples=row["counterExamples"],
            evidence=row["evidence"],
            activated_in_version=row["activatedInVersion"],
        )
        for row in context["legacyProvenance"]
    }
    return EmpiricalRules(
        entries=entries,
        system_item_ids=frozenset(context["systemItemIds"]),
        inheritance=context["inheritance"]["mode"],
    )


_V11 = 11


@contextlib.contextmanager
def _as_v11(monkeypatch):
    """A ponte do contexto legado v10 só atua na cópia v10 -> v11; a v12 copia da v11.
    Os testes da ponte simulam a versão em que ela atua, sem mudar o código."""
    with monkeypatch.context() as patched:
        patched.setattr("riftlab.timeline.application.RECONSTRUCTION_ALGORITHM_VERSION", _V11)
        patched.setattr("riftlab.timeline.application.CARRY_FORWARD_FROM_VERSION", 10)
        yield


async def _seed_legacy_v10_match(session_factory, tmp_path, match_id, monkeypatch):
    await _import_demo_match(session_factory, tmp_path, match_id)
    raw_catalog = await DemoCatalogSource().load_catalog("DEMO-1")

    class _PatchCatalogSource:
        async def load_catalog(self, game_patch: str):
            return replace(raw_catalog, game_patch=game_patch)

    await sync_catalog(session_factory, _PatchCatalogSource(), "16.19")
    mapping = CatalogMapping(
        schema_version="v1",
        mapping_version=raw_catalog.mapping_version,
        entries={"16.19": MappingEntry(raw_catalog.data_dragon_version)},
    )
    async with unit_of_work(session_factory) as session:
        match = await session.get(MatchModel, match_id)
        assert match is not None
        match.patch = "16.19"
    rules = _legacy_v10_rules()
    from riftlab.timeline import rule_resolution

    original_resolve = rule_resolution.resolve_rules

    async def resolve_v10_without_persisted_parameter(
        session, empirical_rules, patch, catalog_id, queue=None
    ):
        enabled, provenance = await original_resolve(
            session, empirical_rules, patch, catalog_id, queue
        )
        for rule in provenance:
            if rule.get("ruleId") == "R4s":
                rule.pop("systemItemIds", None)
        return enabled, provenance

    with monkeypatch.context() as patched:
        patched.setattr("riftlab.timeline.application.RECONSTRUCTION_ALGORITHM_VERSION", 10)
        patched.setattr(rule_resolution, "resolve_rules", resolve_v10_without_persisted_parameter)
        old = await reconstruct_match(
            session_factory, mapping, DemoMatchSource(), match_id, empirical_rules=rules
        )
    expected = legacy_v10_context()["legacyProvenance"]
    assert old.decision_states
    assert all(state.quality["empiricalRules"] == expected for state in old.decision_states)
    return mapping, rules, old


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


async def _reconstruct_as_previous(session_factory, match_id: str, monkeypatch, **kwargs):
    """Reconstrução gravada na versão anterior (o reducer é o mesmo; só a versão muda)."""
    with monkeypatch.context() as patched:
        patched.setattr(
            "riftlab.timeline.application.RECONSTRUCTION_ALGORITHM_VERSION",
            CARRY_FORWARD_FROM_VERSION,
        )
        return await reconstruct_match(
            session_factory, _DEMO_1_MAPPING, DemoMatchSource(), match_id, **kwargs
        )


@pytest.mark.parametrize("_demo_match", ["DEMO-0002"], indirect=True)
async def test_current_version_carries_previous_states_forward_without_reopening_the_source(
    session_factory, tmp_path, _demo_match, monkeypatch
) -> None:
    """Mesmo catálogo e regras: a versão corrente copia os estados da anterior sem reabrir
    a fonte. Linhas antigas intactas."""
    match_id = "DEMO-0002"
    await _sync_demo_catalog(session_factory)
    await _import_demo_match(session_factory, tmp_path, match_id)
    old = await _reconstruct_as_previous(session_factory, match_id, monkeypatch)
    assert old.decision_states and all(
        s.state_version == CARRY_FORWARD_FROM_VERSION for s in old.decision_states
    )

    carried = await reconstruct_match(
        session_factory, _DEMO_1_MAPPING, _SourceForbiddenIfCalled(), match_id
    )

    assert carried.catalog_id == old.catalog_id
    assert len(carried.decision_states) == len(old.decision_states)
    by_event = {(s.participant_id, s.event_source_index): s for s in old.decision_states}
    for state in carried.decision_states:
        source = by_event[(state.participant_id, state.event_source_index)]
        assert state.state_version == RECONSTRUCTION_ALGORITHM_VERSION and state.id != source.id
        assert state.snapshot == source.snapshot and state.quality == source.quality
    async with unit_of_work(session_factory) as session:
        repo = SqlAlchemyTimelineRepository(session)
        assert await repo.get_match_reconstruction(match_id, CARRY_FORWARD_FROM_VERSION) is not None
        persisted = await repo.list_decision_states(match_id, 1)
    assert {s.id for s in persisted if s.state_version == CARRY_FORWARD_FROM_VERSION} == {
        s.id for s in old.decision_states if s.participant_id == 1
    }
    again = await reconstruct_match(
        session_factory, _DEMO_1_MAPPING, _SourceForbiddenIfCalled(), match_id
    )
    assert {s.id for s in again.decision_states} == {s.id for s in carried.decision_states}


@pytest.mark.parametrize("_demo_match", ["DEMO-0002"], indirect=True)
async def test_current_version_does_not_carry_forward_when_rule_parameters_change(
    session_factory, tmp_path, _demo_match, monkeypatch
) -> None:
    match_id = "DEMO-0002"
    await _sync_demo_catalog(session_factory)
    await _import_demo_match(session_factory, tmp_path, match_id)
    rules_before = EmpiricalRules(
        entries={"R4s": _supported("R4s", frozenset({"DEMO-1"}))},
        system_item_ids=frozenset({7654321}),
    )
    await _reconstruct_as_previous(
        session_factory, match_id, monkeypatch, empirical_rules=rules_before
    )
    rules_now = EmpiricalRules(
        entries={"R4s": _supported("R4s", frozenset({"DEMO-1"}))},
        system_item_ids=frozenset({9876543}),
    )

    with pytest.raises(AssertionError, match="load_match"):
        await reconstruct_match(
            session_factory,
            _DEMO_1_MAPPING,
            _SourceForbiddenIfCalled(),
            match_id,
            empirical_rules=rules_now,
        )


@pytest.mark.parametrize("_demo_match", ["DEMO-0002"], indirect=True)
async def test_verified_v10_context_carries_without_source_and_preserves_v10_rows(
    session_factory, tmp_path, _demo_match, monkeypatch
) -> None:
    match_id = "DEMO-0002"
    mapping, rules, old = await _seed_legacy_v10_match(
        session_factory, tmp_path, match_id, monkeypatch
    )
    before = {
        (state.participant_id, state.event_source_index): state for state in old.decision_states
    }
    DurableRawMatchStore(tmp_path / "raw").purge(match_id)

    with _as_v11(monkeypatch):
        carried = await reconstruct_match(
            session_factory, mapping, _SourceForbiddenIfCalled(), match_id, empirical_rules=rules
        )

    assert len(carried.decision_states) == len(before)
    after_by_key = {
        (state.participant_id, state.event_source_index): state for state in carried.decision_states
    }
    assert after_by_key.keys() == before.keys()
    for key, old_state in before.items():
        new_state = after_by_key[key]
        assert new_state.snapshot == old_state.snapshot
        assert new_state.features == old_state.features
        old_quality = dict(old_state.quality)
        new_quality = dict(new_state.quality)
        old_quality.pop("empiricalRules")
        new_quality.pop("empiricalRules")
        assert new_quality == old_quality
        assert new_state.quality["empiricalRules"] != old_state.quality["empiricalRules"]
        assert next(row for row in new_state.quality["empiricalRules"] if row["ruleId"] == "R4s")[
            "systemItemIds"
        ] == list(legacy_v10_context()["systemItemIds"])

    async with unit_of_work(session_factory) as session:
        repo = SqlAlchemyTimelineRepository(session)
        previous = await repo.get_match_reconstruction(match_id, 10)
        current = await repo.get_match_reconstruction(match_id, _V11)
        all_states = await repo.list_decision_states(match_id, 1)
    assert previous is not None and current is not None
    assert previous.catalog_id == current.catalog_id == old.catalog_id
    assert current.rules_provenance is not None
    assert next(row for row in current.rules_provenance if row["ruleId"] == "R4s")[
        "systemItemIds"
    ] == list(legacy_v10_context()["systemItemIds"])
    persisted_old = [state for state in all_states if state.state_version == 10]
    assert len(persisted_old) == sum(state.participant_id == 1 for state in old.decision_states)
    assert all(
        "systemItemIds"
        not in next(row for row in state.quality["empiricalRules"] if row["ruleId"] == "R4s")
        for state in persisted_old
    )


@pytest.mark.parametrize(
    "divergence", ["unknown-evidence", "historical-parameter-differs-from-current"]
)
@pytest.mark.parametrize("_demo_match", ["DEMO-0002"], indirect=True)
async def test_legacy_bridge_rejects_unknown_or_divergent_v10_context(
    session_factory, tmp_path, _demo_match, monkeypatch, divergence
) -> None:
    match_id = "DEMO-0002"
    mapping, rules, _old = await _seed_legacy_v10_match(
        session_factory, tmp_path, match_id, monkeypatch
    )
    if divergence == "unknown-evidence":
        async with unit_of_work(session_factory) as session:
            states = (
                await session.scalars(
                    select(DecisionStateModel).where(
                        DecisionStateModel.match_id == match_id,
                        DecisionStateModel.state_version == 10,
                    )
                )
            ).all()
            for state in states:
                provenance = [dict(row) for row in state.quality["empiricalRules"]]
                provenance[0]["evidence"] = "evidência divergente"
                state.quality = {**state.quality, "empiricalRules": provenance}
    else:
        rules = EmpiricalRules(
            entries=rules.entries,
            system_item_ids=frozenset({999999}),
            inheritance=rules.inheritance,
        )
    DurableRawMatchStore(tmp_path / "raw").purge(match_id)

    with _as_v11(monkeypatch), pytest.raises(AssertionError, match="load_match"):
        await reconstruct_match(
            session_factory, mapping, _SourceForbiddenIfCalled(), match_id, empirical_rules=rules
        )

    async with unit_of_work(session_factory) as session:
        assert (
            await SqlAlchemyTimelineRepository(session).get_match_reconstruction(match_id, _V11)
            is None
        )


@pytest.mark.parametrize("_demo_match", ["DEMO-0002"], indirect=True)
async def test_current_version_reconstructs_fresh_when_the_patch_now_has_other_rules(
    session_factory, tmp_path, _demo_match, monkeypatch
) -> None:
    """Patch que agora herda (ou ganhou) regras: o resultado pode mudar, então nunca copia;
    sem a fonte, a reconstrução precisa dela e falha em vez de reaproveitar a v9."""
    match_id = "DEMO-0002"
    await _sync_demo_catalog(session_factory)
    await _import_demo_match(session_factory, tmp_path, match_id)
    await _reconstruct_as_previous(session_factory, match_id, monkeypatch)
    rules = EmpiricalRules(entries={"R5": _supported("R5", frozenset({"DEMO-1"}))})

    with pytest.raises(AssertionError, match="load_match"):
        await reconstruct_match(
            session_factory,
            _DEMO_1_MAPPING,
            _SourceForbiddenIfCalled(),
            match_id,
            empirical_rules=rules,
        )
    fresh = await reconstruct_match(
        session_factory, _DEMO_1_MAPPING, DemoMatchSource(), match_id, empirical_rules=rules
    )
    assert fresh.decision_states
    assert all(
        [rule["ruleId"] for rule in s.quality["empiricalRules"]] == ["R5"]
        for s in fresh.decision_states
    )


@pytest.mark.parametrize("_demo_match", ["DEMO-0002"], indirect=True)
async def test_domain_validated_queue_reconstructs_fresh_in_its_catalog_context(
    session_factory, tmp_path, _demo_match, monkeypatch
) -> None:
    """CC-802: confirmação de (patch, fila) em FULL_CANONICAL_V1. A versão anterior foi
    reconstruída com as mesmas regras validadas no patch inteiro (fecho v11); o domínio
    muda origem e contexto, então nada é copiado e a fonte é reaberta em FULL."""
    match_id = "DEMO-0002"
    await _sync_demo_catalog(session_factory)
    await _import_demo_match(session_factory, tmp_path, match_id)
    async with unit_of_work(session_factory) as session:
        match = await session.get(MatchModel, match_id)
        assert match is not None
        queue = match.queue
    patch_wide = EmpiricalRules(entries={"R5": _supported("R5", frozenset({"DEMO-1"}))})
    await _reconstruct_as_previous(
        session_factory, match_id, monkeypatch, empirical_rules=patch_wide
    )
    entry = _supported("R5", frozenset())
    domain_rules = EmpiricalRules(
        entries={
            "R5": replace(
                entry, domains_validated={("DEMO-1", queue): (0.0, 0, "linha do domínio")}
            )
        },
        domains={("DEMO-1", queue): DomainEntry("DEMO-1", queue, "FULL_CANONICAL_V1", "domínio")},
    )

    with pytest.raises(AssertionError, match="load_match"):
        await reconstruct_match(
            session_factory,
            _DEMO_1_MAPPING,
            _SourceForbiddenIfCalled(),
            match_id,
            empirical_rules=domain_rules,
        )
    fresh = await reconstruct_match(
        session_factory, _DEMO_1_MAPPING, DemoMatchSource(), match_id, empirical_rules=domain_rules
    )

    assert fresh.catalog_context is not None
    assert fresh.catalog_context.strategy.value == "FULL_CANONICAL_V1"
    assert fresh.decision_states
    for state in fresh.decision_states:
        [rule] = state.quality["empiricalRules"]
        assert (rule["ruleId"], rule["application"], rule["appliedFromPatch"]) == (
            "R5",
            "VALIDATED",
            "DEMO-1",
        )
        assert (rule["appliedFromQueue"], rule["catalogContext"]) == (queue, "FULL_CANONICAL_V1")
        assert rule["evidence"] == "linha do domínio"
    # A outra fila do mesmo patch não usa o domínio.
    assert domain_rules.resolve("DEMO-1", 440 if queue == 420 else 420).rule_ids == frozenset()
