"""Seleção retrospectiva, elegibilidade, orçamento e features v1 (CC-204).
Estados vêm do reducer real (em memória), nunca de payloads
escritos à mão, exceto onde o teste fabrica um estado inválido de propósito.
Sem I/O de banco e sem rede."""

from __future__ import annotations

import dataclasses
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pytest

from riftlab.catalog.classification import ItemClass, classify_catalog
from riftlab.catalog.domain import CatalogVersion, Item
from riftlab.catalog.infrastructure.demo_source import DemoCatalogSource
from riftlab.decisions.domain import (
    DecisionInputError,
    IncompatibleStateError,
    MatchContext,
    ParticipantView,
)
from riftlab.decisions.features import (
    FEATURE_ALLOWLIST,
    FORBIDDEN_FIELD_NAMES,
    PreDecisionView,
    build_pre_decision_view,
    extract_features,
)
from riftlab.decisions.scope import DEMO_SCOPE, DecisionScope
from riftlab.decisions.selection import DecisionPoint, evaluate_decision, point_is_actionable
from riftlab.ingestion.domain import Match, Participant
from riftlab.ingestion.infrastructure.demo_source import DemoMatchSource
from riftlab.shared.domain import SourceOrigin
from riftlab.timeline.application import (
    RECONSTRUCTION_ALGORITHM_VERSION,
    _decision_state_from_purchase,
    reconstruct_participant_purchases,
)
from riftlab.timeline.domain import DecisionState, TimelineEvent

MATCH_ID = "MATCH-D"
CATALOG_ID = uuid.UUID(int=7)
P1 = 1
DEMO, REAL = SourceOrigin.CURATED_DEMO, SourceOrigin.COMPLETED_MATCH


@dataclass(frozen=True, slots=True)
class _Frame:
    timestamp_ms: int
    participant_frames: dict[int, dict[str, Any]] = field(default_factory=dict)


def _item(item_id: int, recipe: tuple[int, ...] = (), into: tuple[int, ...] = (), tags=()) -> Item:
    return Item(
        CATALOG_ID,
        item_id,
        1000,
        [{"itemId": c, "quantity": 1} for c in recipe],
        {"name": "x", "tags": list(tags), "stats": {}, "into": list(into)},
        {"purchasable": True, "maps": [11]},
    )


# 1001/1002 componentes; 3001/3002/3003 principais; 1003 botas; 2003 consumível;
# 3340 trinket.
CATALOG_ITEMS = {
    1001: _item(1001, into=(3001, 3002)),
    1002: _item(1002, into=(3001, 3003)),
    1003: _item(1003, tags=("Boots",)),
    2003: _item(2003, tags=("Consumable",)),
    3340: _item(3340, tags=("Trinket",)),
    3001: _item(3001, recipe=(1001, 1002)),
    3002: _item(3002, recipe=(1001,)),
    3003: _item(3003, recipe=(1002,)),
}
CURATION = "data_dragon_v5"
TABLE = classify_catalog(CATALOG_ITEMS, curation_rules_version=CURATION)
CATALOG_ROW = CatalogVersion(CATALOG_ID, "P-1", "dd-1", "s" * 64, "v1", "r" * 64, CURATION)

PARTICIPANTS = (
    ParticipantView(1, 100, 100, "BOTTOM"),
    ParticipantView(2, 101, 100, "TOP"),
    ParticipantView(6, 200, 200, "BOTTOM"),
    ParticipantView(7, 201, 200, "TOP"),
)
MATCH = MatchContext(MATCH_ID, 420, 11, "P-1")


def _scope(origin: SourceOrigin = DEMO, **overrides: Any) -> DecisionScope:
    base = dict(
        version="test_scope_v1",
        origin=origin,
        queues=frozenset({420}),
        map_id=11,
        patches=frozenset({"P-1"}),
        role="BOTTOM",
        champion_ids=frozenset({100}),
    )
    base.update(overrides)
    return DecisionScope(**base)  # type: ignore[arg-type]


def test_queue_is_in_scope_when_it_belongs_to_the_scope_queues() -> None:
    scope = _scope(queues=frozenset({420, 440}))
    flex = MatchContext(MATCH_ID, 440, 11, "P-1")
    aram = MatchContext(MATCH_ID, 450, 11, "P-1")

    assert scope.out_of_scope_reasons(flex, PARTICIPANTS[0]) == ()
    assert "QUEUE_OUT_OF_SCOPE" in scope.out_of_scope_reasons(aram, PARTICIPANTS[0])


def _ev(source_index: int, ts: int, type_: str, **payload: Any) -> TimelineEvent:
    return TimelineEvent(MATCH_ID, source_index, ts, type_, payload)


def _buy(source_index: int, ts: int, item_id: int) -> TimelineEvent:
    return _ev(source_index, ts, "ITEM_PURCHASED", participantId=P1, itemId=item_id)


def _states(
    events: list[TimelineEvent],
    *,
    frames: list[_Frame] | None = None,
    origin: SourceOrigin = DEMO,
    with_catalog: bool = True,
    items: dict[int, Item] | None = None,
    excluded: frozenset[int] = frozenset(),
) -> list[DecisionState]:
    purchases = reconstruct_participant_purchases(
        participant_id=P1,
        events=events,
        origin=origin,
        frames=frames or (),
        catalog_id=CATALOG_ID if with_catalog else None,
        catalog_items=(items or CATALOG_ITEMS) if with_catalog else {},
        excluded_item_ids=excluded,
    )
    catalog_id = CATALOG_ID if with_catalog else None
    return [_decision_state_from_purchase(MATCH_ID, catalog_id, origin, p) for p in purchases]


def _evaluate(
    state: DecisionState,
    *,
    scope: DecisionScope | None = None,
    with_catalog: bool = True,
    table=TABLE,
    row: CatalogVersion = CATALOG_ROW,
    participants: tuple[ParticipantView, ...] = PARTICIPANTS,
    match: MatchContext = MATCH,
) -> DecisionPoint | None:
    origin = SourceOrigin(state.quality["sourceOrigin"])
    return evaluate_decision(
        state,
        participants=participants,
        match=match,
        catalog=row if with_catalog else None,
        classification=table if with_catalog else None,
        scope=scope or _scope(origin),
    )


def _points(states: list[DecisionState], **kwargs: Any) -> list[DecisionPoint]:
    return [p for s in states if (p := _evaluate(s, **kwargs)) is not None]


def _codes(point: DecisionPoint) -> dict[str, tuple[str, ...]]:
    return {reason.code: reason.sub_reasons for reason in point.reasons}


# ---------------------------------------------------------------------------
# Ordinal do item principal, multiplicidade e trinket
# ---------------------------------------------------------------------------


def test_first_main_item_is_ordinal_one_and_eligible() -> None:
    states = _states([_buy(0, 1000, 1001), _buy(1, 2000, 1002), _buy(2, 3000, 3001)])
    points = _points(states)
    assert len(points) == 1  # só a compra do item principal gera ponto
    [point] = points
    assert (point.target_item_id, point.target_class) == (3001, ItemClass.MAIN)
    assert point.main_item_ordinal == 1
    assert point.eligible and point.reasons == () and point.features is not None
    assert point.features.values["main_item_ordinal"] == 1


def test_second_main_item_is_ordinal_two_and_eligible() -> None:
    states = _states([_buy(0, 1000, 3001), _buy(1, 2000, 3002)])
    first, second = _points(states)
    assert (first.main_item_ordinal, second.main_item_ordinal) == (1, 2)
    assert second.eligible


def test_third_main_item_is_listed_out_of_scope_without_features() -> None:
    states = _states([_buy(0, 1000, 3001), _buy(1, 2000, 3002), _buy(2, 3000, 3003)])
    third = _points(states)[2]
    assert not third.eligible and third.features is None
    assert _codes(third) == {"OUT_OF_SCOPE": ("MAIN_ITEM_ORDINAL_BEYOND_SECOND",)}


def test_two_units_of_the_same_main_item_count_by_quantity() -> None:
    states = _states([_buy(0, 1000, 3001), _buy(1, 2000, 3001), _buy(2, 3000, 3002)])
    third = _points(states)[2]
    assert third.main_item_ordinal == 3
    assert "OUT_OF_SCOPE" in _codes(third)


def test_components_boots_consumables_and_trinkets_do_not_count_or_generate_points() -> None:
    events = [_buy(0, 1000, 1003), _buy(1, 1500, 2003), _buy(2, 1800, 3340), _buy(3, 2000, 3001)]
    states = _states(events)
    assert len(states) == 4
    assert [p.target_item_id for p in _points(states)] == [3001]
    [point] = _points(states)
    assert point.main_item_ordinal == 1  # bota/consumível/trinket não contam
    assert point.features is not None
    assert point.features.values["inventory_trinket_item_id"] == 3340  # trinket preservado
    assert [1003, 1] in point.features.values["inventory_items"]  # multiconjunto preservado


def test_known_non_main_targets_never_generate_a_point() -> None:
    states = _states([_buy(0, 1000, 1001), _buy(1, 1100, 1003), _buy(2, 1200, 2003)])
    assert _points(states) == []


# ---------------------------------------------------------------------------
# Alvo desconhecido/excluído/não classificado/sem catálogo
# ---------------------------------------------------------------------------


def test_unknown_target_is_listed_ineligible() -> None:
    [state] = _states([_buy(0, 1000, 9999)])
    point = _evaluate(state)
    assert point is not None and not point.eligible
    assert point.target_class is ItemClass.UNKNOWN
    assert _codes(point)["TARGET_NOT_CLASSIFIABLE"] == ("UNKNOWN", "ITEM_NOT_IN_CATALOG")


def test_excluded_target_is_listed_ineligible() -> None:
    [state] = _states([_buy(0, 1000, 8888)], excluded=frozenset({8888}))
    table = classify_catalog(
        CATALOG_ITEMS, curation_rules_version=CURATION, excluded_item_ids=frozenset({8888})
    )
    point = _evaluate(state, table=table)
    assert point is not None and point.target_class is ItemClass.EXCLUDED
    assert _codes(point)["TARGET_NOT_CLASSIFIABLE"][0] == "EXCLUDED"


def test_unclassified_target_under_unsupported_curation_is_listed_ineligible() -> None:
    legacy_row = dataclasses.replace(CATALOG_ROW, curation_rules_version="data_dragon_v4")
    legacy_table = classify_catalog(CATALOG_ITEMS, curation_rules_version="data_dragon_v4")
    [state] = _states([_buy(0, 1000, 3001)])
    point = _evaluate(state, table=legacy_table, row=legacy_row)
    assert point is not None and not point.eligible
    assert point.target_class is ItemClass.UNCLASSIFIED
    assert _codes(point)["TARGET_NOT_CLASSIFIABLE"][1] == "CLASSIFICATION_UNSUPPORTED_CURATION"
    assert point.versions.curation_rules_version == "data_dragon_v4"  # DA LINHA, não a constante


def test_owned_unclassifiable_item_makes_inventory_uncertain() -> None:
    # 9999 desconhecido comprado antes: fica no inventário e não é classificável
    states = _states([_buy(0, 1000, 9999), _buy(1, 2000, 3001)])
    point = _evaluate(states[1])
    assert point is not None and not point.eligible and point.main_item_ordinal is None
    assert "OWNED_ITEM_UNCLASSIFIABLE" in _codes(point)["INVENTORY_UNCERTAIN"]
    assert "ITEM_UNKNOWN" in _codes(point)["INVENTORY_UNCERTAIN"]  # sub-motivo pré-decisão literal


def test_missing_catalog_lists_the_point_with_literal_sub_reason() -> None:
    [state] = _states([_buy(0, 1000, 3001)], with_catalog=False, origin=REAL)
    point = _evaluate(state, with_catalog=False, scope=_scope(REAL))
    assert point is not None and not point.eligible
    assert point.target_class is None and point.main_item_ordinal is None
    assert _codes(point)["MISSING_CATALOG"] == ("NO_CATALOG",)
    assert point.versions.catalog_id is None and point.versions.classification_version is None


# ---------------------------------------------------------------------------
# Alvo revertido/incerto (seleção retrospectiva)
# ---------------------------------------------------------------------------


def test_reverted_main_target_is_listed_ineligible() -> None:
    states = _states(
        [_buy(0, 1000, 3001), _ev(1, 1100, "ITEM_UNDO", participantId=P1, beforeId=3001, afterId=0)]
    )
    point = _evaluate(states[0])
    assert point is not None and not point.eligible
    assert _codes(point) == {"TARGET_REVERTED": ("REVERTED_BY_UNDO",)}


def test_reverted_target_in_real_data_carries_the_unvalidated_correlation_reason() -> None:
    states = _states(
        [
            _buy(0, 1000, 3001),
            _ev(1, 1100, "ITEM_UNDO", participantId=P1, beforeId=3001, afterId=0),
        ],
        origin=REAL,
    )
    point = _evaluate(states[0], scope=_scope(REAL))
    assert point is not None
    assert _codes(point)["TARGET_REVERTED"] == ("REVERTED_BY_UNDO", "UNVALIDATED_UNDO_SEMANTICS")


def test_uncertain_status_target_is_listed_ineligible() -> None:
    states = _states(
        [_buy(0, 1000, 3001), _ev(1, 1100, "ITEM_UNDO", participantId=P1, beforeId=9999, afterId=0)]
    )
    point = _evaluate(states[0])
    assert point is not None and not point.eligible
    assert _codes(point) == {"TARGET_STATUS_UNCERTAIN": ("STATUS_UNCERTAIN",)}


def test_reverted_non_main_target_generates_no_point() -> None:
    states = _states(
        [_buy(0, 1000, 1001), _ev(1, 1100, "ITEM_UNDO", participantId=P1, beforeId=1001, afterId=0)]
    )
    assert _points(states) == []


# ---------------------------------------------------------------------------
# REAL sintético inelegível com motivos preservados
# ---------------------------------------------------------------------------


def test_real_data_after_an_undo_is_ineligible_with_the_reason_preserved_literally() -> None:
    events = [
        _buy(0, 1000, 1001),
        _buy(1, 1500, 3002),
        _ev(2, 1600, "ITEM_UNDO", participantId=P1, beforeId=3002, afterId=0),
        _buy(3, 2000, 3003),
    ]
    states = _states(events, origin=REAL)  # 3 compras: 1001, 3002 e 3003
    point = _evaluate(states[2], scope=_scope(REAL))
    assert point is not None and not point.eligible and point.features is None
    assert "UNVALIDATED_UNDO_SEMANTICS" in _codes(point)["INVENTORY_UNCERTAIN"]
    # o mesmo cenário em DEMO (regra sintética verificada) segue elegível
    demo_point = _evaluate(_states(events)[2])
    assert demo_point is not None and demo_point.eligible


def test_cumulative_quality_never_decides_context_eligibility() -> None:
    """A qualidade cumulativa da PRÓPRIA compra pode estar suja pelo alvo (item
    desconhecido, capacidade), mas só a preDecisionQuality decide o contexto."""
    states = _states([_buy(0, 1000, 3001)])
    state = states[0]
    dirty = dataclasses.replace(
        state,
        quality={
            **state.quality,
            "uncertain": True,
            "reasons": [{"code": "ITEM_UNKNOWN", "itemId": 1}],
        },
    )
    clean_point, dirty_point = _evaluate(state), _evaluate(dirty)
    assert clean_point is not None and dirty_point is not None
    assert clean_point.eligible and dirty_point.eligible


# ---------------------------------------------------------------------------
# Recorte
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("scope_overrides", "match", "participants", "expected"),
    [
        ({}, MatchContext(MATCH_ID, 450, 11, "P-1"), PARTICIPANTS, ("QUEUE_OUT_OF_SCOPE",)),
        ({}, MatchContext(MATCH_ID, 420, 12, "P-1"), PARTICIPANTS, ("MAP_OUT_OF_SCOPE",)),
        ({}, MatchContext(MATCH_ID, 420, 11, "P-9"), PARTICIPANTS, ("PATCH_OUT_OF_SCOPE",)),
        ({"role": "TOP"}, MATCH, PARTICIPANTS, ("ROLE_OUT_OF_SCOPE",)),
        ({"champion_ids": frozenset({555})}, MATCH, PARTICIPANTS, ("CHAMPION_OUT_OF_SCOPE",)),
        (
            {},
            MATCH,
            (ParticipantView(1, 100, 100, None), *PARTICIPANTS[1:]),
            ("ROLE_UNKNOWN",),
        ),
        (
            {},
            MatchContext(MATCH_ID, 450, 12, "P-9"),
            PARTICIPANTS,
            ("QUEUE_OUT_OF_SCOPE", "MAP_OUT_OF_SCOPE", "PATCH_OUT_OF_SCOPE"),
        ),
    ],
)
def test_out_of_scope_sub_reasons_are_preserved(
    scope_overrides, match, participants, expected
) -> None:
    [state] = _states([_buy(0, 1000, 3001)])
    point = _evaluate(
        state, scope=_scope(**scope_overrides), match=match, participants=participants
    )
    assert point is not None and not point.eligible
    assert _codes(point)["OUT_OF_SCOPE"] == expected


def test_demo_scope_is_explicit_and_distinct_from_real_scope() -> None:
    assert DEMO_SCOPE.origin is DEMO and DEMO_SCOPE.version == "demo_scope_v1"
    [state] = _states([_buy(0, 1000, 3001)], origin=REAL)
    with pytest.raises(DecisionInputError) as excinfo:  # recorte demo nunca aplicado a dado real
        _evaluate(state, scope=DEMO_SCOPE)
    assert excinfo.value.code == "SCOPE_ORIGIN_MISMATCH"


# ---------------------------------------------------------------------------
# Orçamento: separado da elegibilidade
# ---------------------------------------------------------------------------


def _gold_frames(age_ms: int, event_ts: int = 200_000) -> list[_Frame]:
    return [_Frame(event_ts - age_ms, {P1: {"currentGold": 640, "level": 4}})]


@pytest.mark.parametrize(("age_ms", "stale"), [(89_999, False), (90_000, False), (90_001, True)])
def test_stale_boundary_90000_is_fresh_90001_is_stale(age_ms: int, stale: bool) -> None:
    [state] = _states([_buy(0, 200_000, 3001)], frames=_gold_frames(age_ms))
    point = _evaluate(state)
    assert point is not None and point.eligible  # nunca inelegível por orçamento
    assert point.budget.stale is stale
    assert point.budget.value == 640 and point.budget.age_ms == age_ms  # valor preservado
    assert point.budget.origin == "FRAME_ESTIMATE"
    assert point.budget.codes == (("STALE_FRAME",) if stale else ())
    assert point.budget.immediate_purchase_assertable is (not stale)
    assert point_is_actionable(point) is (not stale)


def test_no_prior_frame_is_unknown_budget_null_never_zero() -> None:
    [state] = _states([_buy(0, 1000, 3001)])
    point = _evaluate(state)
    assert point is not None and point.eligible
    assert point.budget.value is None and point.budget.age_ms is None
    assert (
        point.budget.codes == ("UNKNOWN_BUDGET",) and not point.budget.immediate_purchase_assertable
    )
    assert point.features is not None
    assert point.features.values["gold_estimate"] is None
    assert point.features.masks["gold_estimate"] == "NO_PRIOR_FRAME"
    assert point.features.values["gold_stale"] is None
    assert point.features.masks["gold_stale"] == "NO_PRIOR_FRAME"


@pytest.mark.parametrize(
    "frame_content",
    [{}, {2: {"currentGold": 5}}, {P1: {"currentGold": -5}}, {P1: {"currentGold": True}}],
)
def test_frame_without_valid_gold_is_unknown_budget_with_age_preserved(frame_content) -> None:
    [state] = _states([_buy(0, 100_000, 3001)], frames=[_Frame(40_000, frame_content)])
    point = _evaluate(state)
    assert point is not None and point.eligible
    assert point.budget.value is None and point.budget.age_ms == 60_000
    assert point.budget.codes == ("UNKNOWN_BUDGET",)
    assert point.features is not None
    assert point.features.masks["gold_estimate"] == "FIELD_ABSENT"
    assert point.features.values["gold_age_ms"] == 60_000  # idade do frame preservada


def test_stale_and_unknown_can_coexist_and_both_are_reported() -> None:
    [state] = _states([_buy(0, 500_000, 3001)], frames=[_Frame(0, {})])
    point = _evaluate(state)
    assert point is not None and point.eligible
    assert point.budget.codes == ("UNKNOWN_BUDGET", "STALE_FRAME")


# ---------------------------------------------------------------------------
# Features: nulos/máscaras, composição, ordem determinística
# ---------------------------------------------------------------------------


def test_features_carry_masks_provenance_and_exact_allowlist() -> None:
    [state] = _states(
        [_buy(0, 100_000, 3001)], frames=[_Frame(60_000, {P1: {"currentGold": 900, "level": 5}})]
    )
    point = _evaluate(state)
    assert point is not None and point.features is not None
    features = point.features
    assert tuple(features.values) == FEATURE_ALLOWLIST == tuple(features.masks)
    assert features.values["level"] == 5 and features.masks["level"] == "PRESENT"
    assert features.values["level_age_ms"] == 40_000
    assert features.values["game_time_ms"] == 100_000
    assert features.values["gold_stale"] is False
    assert features.values["patch"] == "P-1"
    for name in ("ally_composition_aggregates", "enemy_composition_aggregates"):
        assert features.values[name] is None and features.masks[name] == "UNAVAILABLE_IN_SCHEMA"
    payload = features.to_payload()
    assert payload["featureSchemaVersion"] == "feature_schema_v1"
    assert set(payload["provenance"]) == set(FEATURE_ALLOWLIST)


def test_level_absence_is_masked_not_zero() -> None:
    [no_frame] = _states([_buy(0, 1000, 3001)])
    [no_level] = _states(
        [_buy(0, 100_000, 3001)], frames=[_Frame(50_000, {P1: {"currentGold": 10}})]
    )
    a, b = _evaluate(no_frame), _evaluate(no_level)
    assert a is not None and a.features is not None and b is not None and b.features is not None
    assert (a.features.values["level"], a.features.masks["level"]) == (None, "NO_PRIOR_FRAME")
    assert (b.features.values["level"], b.features.masks["level"]) == (None, "FIELD_ABSENT")
    assert b.features.values["level_age_ms"] == 50_000


def test_champion_lists_are_deterministic_and_sorted_regardless_of_participant_order() -> None:
    [state] = _states([_buy(0, 1000, 3001)])
    shuffled = tuple(reversed(PARTICIPANTS))
    a, b = _evaluate(state), _evaluate(state, participants=shuffled)
    assert a is not None and a.features is not None and b is not None and b.features is not None
    assert a.features == b.features
    assert a.features.values["ally_champion_ids"] == [101]  # exclui o próprio
    assert a.features.values["enemy_champion_ids"] == [200, 201]
    assert a.features.values["self_champion_id"] == 100


# ---------------------------------------------------------------------------
# Vazamento: perturbação do futuro, do alvo e de campos finais
# ---------------------------------------------------------------------------

_FRAMES = [
    _Frame(0, {P1: {"currentGold": 500, "level": 1}}),
    _Frame(60_000, {P1: {"currentGold": 800, "level": 3}}),
]


def _features_of(events: list[TimelineEvent], frames: list[_Frame], at: int) -> dict[str, Any]:
    """Features calculadas DIRETAMENTE da visão pré-decisão, independente da
    elegibilidade: a seleção retrospectiva pode mudar com o futuro (um undo
    posterior torna o alvo incerto), mas as features não podem."""
    states = _states(events, frames=frames)
    state = next(s for s in states if s.event_source_index == at)
    point = _evaluate(state)
    assert point is not None and point.main_item_ordinal is not None
    return extract_features(
        build_pre_decision_view(state),
        participants=PARTICIPANTS,
        match=MATCH,
        main_item_ordinal=point.main_item_ordinal,
    ).to_payload()


def test_features_identical_when_only_the_target_changes_with_the_same_prefix() -> None:
    prefix = [_buy(0, 30_000, 1001), _buy(1, 40_000, 1002)]
    a = _features_of([*prefix, _buy(2, 100_000, 3001)], _FRAMES, 2)
    b = _features_of([*prefix, _buy(2, 100_000, 3002)], _FRAMES, 2)
    c = _features_of([*prefix, _buy(2, 100_000, 3003)], _FRAMES, 2)
    assert a == b == c


def test_features_identical_when_future_events_and_frames_are_perturbed() -> None:
    prefix = [_buy(0, 30_000, 1001), _buy(1, 100_000, 3001)]
    futures = [
        [],
        [_buy(2, 120_000, 3002), _buy(3, 130_000, 3003)],
        [
            _ev(2, 110_000, "ITEM_SOLD", participantId=P1, itemId=1001),
            _ev(3, 111_000, "ITEM_UNDO", participantId=P1, beforeId=0, afterId=1001),
        ],
        [_ev(2, 100_000, "ITEM_DESTROYED", participantId=P1, itemId=1001), _buy(3, 100_500, 2003)],
    ]
    frame_futures = [
        _FRAMES,
        [
            *_FRAMES,
            _Frame(100_000, {P1: {"currentGold": 1, "level": 18}}),
        ],  # mesmo instante: nunca usado
        [*_FRAMES, _Frame(160_000, {P1: {"currentGold": 9999, "level": 18}})],  # futuro
    ]
    baseline = _features_of(prefix, _FRAMES, 1)
    for future in futures:
        for frames in frame_futures:
            assert _features_of([*prefix, *future], frames, 1) == baseline


def test_a_later_uncorrelated_undo_may_change_selection_but_never_the_features() -> None:
    """Seleção é retrospectiva (pode mudar com o futuro); features não."""
    prefix = [_buy(0, 30_000, 1001), _buy(1, 100_000, 3001)]
    later_undo = [_ev(2, 110_000, "ITEM_UNDO", participantId=P1, beforeId=0, afterId=1001)]
    plain = _evaluate(_states(prefix, frames=_FRAMES)[1])
    perturbed = _evaluate(_states([*prefix, *later_undo], frames=_FRAMES)[1])
    assert plain is not None and plain.eligible
    assert perturbed is not None and not perturbed.eligible
    assert _codes(perturbed) == {"TARGET_STATUS_UNCERTAIN": ("STATUS_UNCERTAIN",)}
    assert _features_of(prefix, _FRAMES, 1) == _features_of([*prefix, *later_undo], _FRAMES, 1)


def test_features_identical_when_target_and_final_fields_of_the_state_are_perturbed() -> None:
    [state] = _states([_buy(0, 100_000, 3001)], frames=_FRAMES)
    baseline = _evaluate(state)
    assert baseline is not None and baseline.features is not None
    perturbed = dataclasses.replace(
        state,
        snapshot={**state.snapshot, "purchasedItemId": 3002, "finalInventory": {"3001": 6}},
        quality={
            **state.quality,
            "uncertain": True,
            "reasons": [{"code": "ITEM_UNKNOWN", "itemId": 9}],
            "retrospectiveTarget": {
                "status": "COMPLETED",
                "undoSourceIndex": 99,
                "correlationUnvalidated": True,
            },
            "win": True,
            "kills": 17,
            "durationSeconds": 2100,
            "finalGold": 40000,
        },
        features={"leak": 1},
    )
    other = _evaluate(perturbed)
    assert other is not None and other.features is not None
    assert other.features == baseline.features  # nada disso chega às features


def test_pre_decision_view_and_projections_have_no_forbidden_fields() -> None:
    for cls in (PreDecisionView, ParticipantView, MatchContext):
        names = {f.name for f in dataclasses.fields(cls)}
        assert not names & FORBIDDEN_FIELD_NAMES, (cls.__name__, names & FORBIDDEN_FIELD_NAMES)
    assert "subject_hash" not in {f.name for f in dataclasses.fields(ParticipantView)}


def test_participant_view_drops_the_subject_hash_and_match_context_drops_the_outcome() -> None:
    participant = Participant(MATCH_ID, 1, 100, 100, "hash-secreto-nunca-copiado", "BOTTOM")
    view = ParticipantView.from_participant(participant)
    assert "hash-secreto-nunca-copiado" not in repr(view)
    match = Match(MATCH_ID, 420, 11, "P-1", datetime(2026, 1, 1, tzinfo=UTC), 2100, "raw")
    context = MatchContext.from_match(match)
    assert not hasattr(context, "duration_seconds") and "2100" not in repr(context)


def test_view_built_from_a_state_never_exposes_the_target_or_retrospective_block() -> None:
    [state] = _states([_buy(0, 100_000, 3001)], frames=_FRAMES)
    view = build_pre_decision_view(state)
    rendered = repr(view)
    assert "3001" not in rendered  # o alvo não está na visão (inventário anterior está vazio)
    assert "retrospective" not in rendered.lower() and "purchased" not in rendered.lower()


# ---------------------------------------------------------------------------
# Versões, estados incompatíveis e consistência de entradas
# ---------------------------------------------------------------------------


def test_every_output_carries_all_versions() -> None:
    [state] = _states([_buy(0, 1000, 3001)])
    point = _evaluate(state)
    assert point is not None
    assert point.versions.to_payload() == {
        "reconstructionVersion": RECONSTRUCTION_ALGORITHM_VERSION,
        "selectionVersion": "decision_selection_v2",
        "featureSchemaVersion": "feature_schema_v1",
        "scopeVersion": "test_scope_v1",
        "catalogId": str(CATALOG_ID),
        "curationRulesVersion": CURATION,
        "classificationVersion": "item_classification_v1",
    }


@pytest.mark.parametrize("old_version", [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 14])
def test_state_of_another_reconstruction_version_is_rejected_without_fallback(
    old_version: int,
) -> None:
    [state] = _states([_buy(0, 1000, 3001)])
    old = dataclasses.replace(state, state_version=old_version)
    with pytest.raises(IncompatibleStateError) as excinfo:
        _evaluate(old)
    assert excinfo.value.code == "STATE_VERSION_INCOMPATIBLE"
    with pytest.raises(IncompatibleStateError):
        build_pre_decision_view(old)


def test_v4_shaped_state_without_v5_fields_is_rejected_as_incompatible_not_reinterpreted() -> None:
    [state] = _states([_buy(0, 1000, 3001)])
    quality = {
        k: v
        for k, v in state.quality.items()
        if k not in ("levelObservation", "retrospectiveTarget")
    }
    snapshot = {k: v for k, v in state.snapshot.items() if k != "eventTimestampMs"}
    v4 = dataclasses.replace(state, state_version=4, quality=quality, snapshot=snapshot)
    with pytest.raises(IncompatibleStateError) as excinfo:
        _evaluate(v4)
    assert excinfo.value.code == "STATE_VERSION_INCOMPATIBLE"


@pytest.mark.parametrize(
    "missing", ["levelObservation", "preDecisionQuality", "goldEstimate", "retrospectiveTarget"]
)
def test_current_version_state_missing_a_required_block_is_malformed(missing: str) -> None:
    [state] = _states([_buy(0, 1000, 3001)])
    broken = dataclasses.replace(
        state, quality={k: v for k, v in state.quality.items() if k != missing}
    )
    with pytest.raises(IncompatibleStateError) as excinfo:
        _evaluate(broken)
    assert excinfo.value.code == "STATE_MALFORMED"


def test_inconsistent_inputs_are_errors_not_silently_ignored() -> None:
    [state] = _states([_buy(0, 1000, 3001)])
    other_row = dataclasses.replace(CATALOG_ROW, id=uuid.UUID(int=99))
    with pytest.raises(DecisionInputError) as catalog_error:
        _evaluate(state, row=other_row)
    assert catalog_error.value.code == "CATALOG_MISMATCH"
    stale_table = classify_catalog(CATALOG_ITEMS, curation_rules_version="data_dragon_v4")
    with pytest.raises(DecisionInputError) as classification_error:
        _evaluate(state, table=stale_table)  # tabela v4 com linha de catálogo v5
    assert classification_error.value.code == "CLASSIFICATION_MISMATCH"
    with pytest.raises(DecisionInputError) as participant_error:
        _evaluate(state, participants=PARTICIPANTS[1:])
    assert participant_error.value.code == "PARTICIPANT_NOT_IN_MATCH"
    with pytest.raises(DecisionInputError) as match_error:
        _evaluate(state, match=MatchContext("OTHER", 420, 11, "P-1"))
    assert match_error.value.code == "MATCH_MISMATCH"
    with pytest.raises(DecisionInputError) as missing_catalog:
        evaluate_decision(
            state,
            participants=PARTICIPANTS,
            match=MATCH,
            catalog=None,
            classification=None,
            scope=_scope(),
        )
    assert missing_catalog.value.code == "CATALOG_REQUIRED"


# ---------------------------------------------------------------------------
# DEMO-0002 real (fixtures) pelo reducer v5 em memória
# ---------------------------------------------------------------------------


async def test_demo_0002_is_eligible_with_stale_frame_budget() -> None:
    match_result = await DemoMatchSource().load_match("DEMO-0002")
    catalog_result = await DemoCatalogSource().load_catalog(match_result.patch)
    catalog_id = uuid.uuid4()
    items = {
        dto.item_id: Item(catalog_id, dto.item_id, dto.price, dto.recipe, dto.attributes, dto.flags)
        for dto in catalog_result.items
    }
    row = CatalogVersion(
        catalog_id,
        catalog_result.game_patch,
        catalog_result.data_dragon_version,
        "d" * 64,
        catalog_result.mapping_version,
        catalog_result.raw_hash,
        catalog_result.curation_rules_version,
    )
    table = classify_catalog(items, curation_rules_version=row.curation_rules_version)
    events = [
        TimelineEvent("DEMO-0002", e.source_index, e.timestamp_ms, e.type, e.payload)
        for e in match_result.events
    ]
    purchases = reconstruct_participant_purchases(
        participant_id=1,
        events=events,
        origin=match_result.origin,
        frames=match_result.frames,
        catalog_id=catalog_id,
        catalog_items=items,
    )
    states = [
        _decision_state_from_purchase("DEMO-0002", catalog_id, match_result.origin, p)
        for p in purchases
    ]
    participants = tuple(
        ParticipantView(p.participant_id, p.champion_id, p.team, p.role)
        for p in match_result.participants
    )
    match = MatchContext("DEMO-0002", match_result.queue, match_result.map_id, match_result.patch)

    points = [
        pt
        for s in states
        if (
            pt := evaluate_decision(
                s,
                participants=participants,
                match=match,
                catalog=row,
                classification=table,
                scope=DEMO_SCOPE,
            )
        )
        is not None
    ]
    assert [pt.target_item_id for pt in points] == [
        100010
    ]  # componentes 100001/100002 não geram ponto
    [point] = points
    assert point.eligible and point.reasons == () and point.main_item_ordinal == 1
    assert point.event_timestamp_ms == 472_000
    assert point.budget.age_ms == 472_000 and point.budget.stale  # STALE_FRAME...
    assert (
        point.budget.codes == ("STALE_FRAME",) and point.budget.value == 500
    )  # ...valor preservado
    assert not point_is_actionable(point)  # elegível, mas sem afirmar compra imediata
    assert point.features is not None
    assert point.features.values["level"] == 1 and point.features.values["level_age_ms"] == 472_000
    assert point.features.values["self_champion_id"] == 9001
    assert point.features.values["enemy_champion_ids"] == [9002]
    assert point.versions.scope_version == "demo_scope_v1"
    assert point.versions.curation_rules_version == "curated_demo_v1"


# ---------------------------------------------------------------------------
# O status retrospectivo é avaliado com ou sem catálogo
# ---------------------------------------------------------------------------


def _without_catalog(events: list[TimelineEvent], origin: SourceOrigin = DEMO) -> DecisionPoint:
    [*states] = _states(events, with_catalog=False, origin=origin)
    point = _evaluate(states[0], with_catalog=False, scope=_scope(origin))
    assert point is not None
    return point


def test_reverted_target_without_catalog_lists_both_reasons() -> None:
    """Compra desfeita por undo correlacionado, sem catálogo: TARGET_REVERTED
    deve aparecer junto de MISSING_CATALOG (antes sumia e restava só o segundo)."""
    point = _without_catalog(
        [_buy(0, 1000, 3001), _ev(1, 2000, "ITEM_UNDO", participantId=P1, beforeId=3001, afterId=0)]
    )
    assert not point.eligible
    assert _codes(point) == {
        "TARGET_REVERTED": ("REVERTED_BY_UNDO",),
        "MISSING_CATALOG": ("NO_CATALOG",),
    }


def test_uncertain_status_target_without_catalog_lists_both_reasons() -> None:
    point = _without_catalog(
        [_buy(0, 1000, 3001), _ev(1, 2000, "ITEM_UNDO", participantId=P1, beforeId=9999, afterId=0)]
    )
    assert _codes(point) == {
        "TARGET_STATUS_UNCERTAIN": ("STATUS_UNCERTAIN",),
        "MISSING_CATALOG": ("NO_CATALOG",),
    }


def test_reverted_target_without_catalog_in_real_data_carries_unvalidated_reason() -> None:
    point = _without_catalog(
        [
            _buy(0, 1000, 3001),
            _ev(1, 2000, "ITEM_UNDO", participantId=P1, beforeId=3001, afterId=0),
        ],
        origin=REAL,
    )
    assert _codes(point)["TARGET_REVERTED"] == ("REVERTED_BY_UNDO", "UNVALIDATED_UNDO_SEMANTICS")
    assert "MISSING_CATALOG" in _codes(point)


def test_completed_target_without_catalog_keeps_only_missing_catalog() -> None:
    point = _without_catalog([_buy(0, 1000, 3001)])
    assert _codes(point) == {"MISSING_CATALOG": ("NO_CATALOG",)}
