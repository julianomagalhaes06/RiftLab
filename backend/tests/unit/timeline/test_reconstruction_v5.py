"""Reconstrução v5 (CC-204): nível observado no mesmo frame do ouro, timestamp
do evento de compra e bloco retrospectivo do alvo. O bloco retrospectivo
nunca altera nenhum campo pré-decisão (provado por teste metamórfico de
prefixo fixo). Puro, sem I/O."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

import pytest

from riftlab.catalog.domain import Item
from riftlab.shared.domain import SourceOrigin
from riftlab.timeline.application import (
    RECONSTRUCTION_ALGORITHM_VERSION,
    RETROSPECTIVE_COMPLETED,
    RETROSPECTIVE_REVERTED_BY_UNDO,
    RETROSPECTIVE_STATUS_UNCERTAIN,
    STALE_FRAME_THRESHOLD_MS,
    ReconstructedPurchase,
    _decision_state_from_purchase,
    reconstruct_participant_purchases,
    select_gold_estimate,
    select_level_observation,
)
from riftlab.timeline.domain import TimelineEvent

MATCH_ID = "MATCH-V5"
CATALOG_ID = uuid.uuid4()
P1 = 1


@dataclass(frozen=True, slots=True)
class _Frame:
    timestamp_ms: int
    participant_frames: dict[int, dict[str, Any]] = field(default_factory=dict)


def _event(source_index: int, timestamp_ms: int, type_: str, **payload: Any) -> TimelineEvent:
    return TimelineEvent(MATCH_ID, source_index, timestamp_ms, type_, payload)


def _item(item_id: int, recipe: list[dict[str, Any]] | None = None) -> Item:
    return Item(CATALOG_ID, item_id, 100, recipe or [], {"tags": []}, {})


def _reduce(
    events: list[TimelineEvent],
    *,
    frames: list[_Frame] | None = None,
    origin: SourceOrigin = SourceOrigin.CURATED_DEMO,
    catalog_items: dict[int, Item] | None = None,
) -> list[ReconstructedPurchase]:
    return reconstruct_participant_purchases(
        participant_id=P1,
        events=events,
        origin=origin,
        frames=frames or (),
        catalog_id=CATALOG_ID,
        catalog_items=catalog_items or {},
    )


def test_algorithm_version_is_six() -> None:
    """A v6 (transação destroy→compra) mantém tudo da v5 (nível, timestamp do
    evento, bloco retrospectivo); os testes deste arquivo seguem valendo."""
    assert RECONSTRUCTION_ALGORITHM_VERSION == 6


# --- observação de nível ---------------------------------------------------


def test_level_and_gold_come_from_the_same_frame() -> None:
    frames = [
        _Frame(0, {P1: {"currentGold": 500, "level": 1}}),
        _Frame(60000, {P1: {"currentGold": 800, "level": 3}}),
        _Frame(120000, {P1: {"currentGold": 1200, "level": 5}}),
    ]
    gold = select_gold_estimate(frames, P1, 100000)
    level = select_level_observation(frames, P1, 100000)
    assert (gold.value, level.value) == (800, 3)  # frame de 60000 nos dois
    assert level.frame_timestamp_ms == 60000
    assert level.age_ms == gold.age_ms == 40000
    assert level.origin == "FRAME_OBSERVATION" and level.absent_reason is None


def test_same_timestamp_and_future_frames_never_used_for_level() -> None:
    frames = [_Frame(1000, {P1: {"level": 7}}), _Frame(5000, {P1: {"level": 9}})]
    assert select_level_observation(frames, P1, 1000).absent_reason == "NO_PRIOR_FRAME"
    assert select_level_observation(frames, P1, 3000).value == 7


def test_no_prior_frame_is_absent_never_zero() -> None:
    observation = select_level_observation([], P1, 5000)
    assert observation.value is None
    assert observation.age_ms is None and observation.frame_timestamp_ms is None
    assert observation.origin == "UNKNOWN"
    assert observation.absent_reason == "NO_PRIOR_FRAME"


@pytest.mark.parametrize(
    ("participant_frames", "reason"),
    [
        ({}, "PARTICIPANT_FRAME_MISSING"),
        ({P1: {}}, "PARTICIPANT_FRAME_MISSING"),
        ({2: {"level": 4}}, "PARTICIPANT_FRAME_MISSING"),
        ({P1: {"currentGold": 10}}, "LEVEL_MISSING"),
        ({P1: {"level": None}}, "LEVEL_MISSING"),
        ({P1: {"level": True}}, "LEVEL_INVALID"),
        ({P1: {"level": "5"}}, "LEVEL_INVALID"),
        ({P1: {"level": 5.0}}, "LEVEL_INVALID"),
        ({P1: {"level": 0}}, "LEVEL_INVALID"),
        ({P1: {"level": -3}}, "LEVEL_INVALID"),
    ],
)
def test_invalid_or_missing_level_is_null_with_explicit_reason(
    participant_frames: dict[int, dict[str, Any]], reason: str
) -> None:
    observation = select_level_observation([_Frame(1000, participant_frames)], P1, 2000)
    assert observation.value is None  # nunca 0
    assert observation.origin == "UNKNOWN"
    assert observation.absent_reason == reason
    assert observation.age_ms == 1000 and observation.frame_timestamp_ms == 1000


def test_level_stale_boundary_matches_gold_boundary() -> None:
    frames = [_Frame(0, {P1: {"currentGold": 10, "level": 2}})]
    at_limit = select_level_observation(frames, P1, STALE_FRAME_THRESHOLD_MS)
    over_limit = select_level_observation(frames, P1, STALE_FRAME_THRESHOLD_MS + 1)
    assert at_limit.stale is False and over_limit.stale is True  # 90000 não é stale; 90001 é
    assert select_gold_estimate(frames, P1, STALE_FRAME_THRESHOLD_MS).stale is False
    assert select_gold_estimate(frames, P1, STALE_FRAME_THRESHOLD_MS + 1).stale is True
    assert over_limit.value == 2  # stale preserva o valor (só sinaliza)


# --- timestamp do evento e projeção persistida -----------------------------


def test_purchase_records_event_timestamp_level_and_persisted_fields() -> None:
    frames = [_Frame(0, {P1: {"currentGold": 500, "level": 2}})]
    events = [_event(0, 30000, "ITEM_PURCHASED", participantId=P1, itemId=1001)]
    [purchase] = _reduce(events, frames=frames, catalog_items={1001: _item(1001)})
    assert purchase.event_timestamp_ms == 30000
    assert purchase.level_observation.value == 2

    state = _decision_state_from_purchase(MATCH_ID, CATALOG_ID, SourceOrigin.CURATED_DEMO, purchase)
    assert state.state_version == RECONSTRUCTION_ALGORITHM_VERSION
    assert state.snapshot["eventTimestampMs"] == 30000
    assert state.quality["levelObservation"]["value"] == 2
    assert state.quality["levelObservation"]["frameTimestampMs"] == 0
    assert state.quality["retrospectiveTarget"] == {
        "status": "COMPLETED",
        "undoSourceIndex": None,
        "correlationUnvalidated": False,
    }
    # goldEstimate continua exatamente como antes (mesmo formato, mesma origem)
    assert state.quality["goldEstimate"] == select_gold_estimate(frames, P1, 30000).to_payload()


# --- bloco retrospectivo ---------------------------------------------------

_CATALOG = {1001: _item(1001), 1002: _item(1002), 1003: _item(1003)}


def _statuses(purchases: list[ReconstructedPurchase]) -> list[str]:
    return [purchase.retrospective_status for purchase in purchases]


def test_completed_purchase_without_undo() -> None:
    events = [
        _event(0, 1000, "ITEM_PURCHASED", participantId=P1, itemId=1001),
        _event(1, 2000, "ITEM_PURCHASED", participantId=P1, itemId=1002),
    ]
    purchases = _reduce(events, catalog_items=_CATALOG)
    assert _statuses(purchases) == [RETROSPECTIVE_COMPLETED, RETROSPECTIVE_COMPLETED]
    assert all(p.retrospective_undo_source_index is None for p in purchases)


def test_correlated_undo_reverts_only_the_undone_purchase() -> None:
    events = [
        _event(0, 1000, "ITEM_PURCHASED", participantId=P1, itemId=1001),
        _event(1, 2000, "ITEM_PURCHASED", participantId=P1, itemId=1002),
        _event(2, 2100, "ITEM_UNDO", participantId=P1, beforeId=1002, afterId=0),
    ]
    purchases = _reduce(events, catalog_items=_CATALOG)
    assert _statuses(purchases) == [RETROSPECTIVE_COMPLETED, RETROSPECTIVE_REVERTED_BY_UNDO]
    assert purchases[1].retrospective_undo_source_index == 2
    assert purchases[1].retrospective_correlation_unvalidated is False  # DEMO sintético


def test_reverted_purchase_in_real_data_is_marked_unvalidated_correlation() -> None:
    events = [
        _event(0, 1000, "ITEM_PURCHASED", participantId=P1, itemId=1001),
        _event(1, 1100, "ITEM_UNDO", participantId=P1, beforeId=1001, afterId=0),
    ]
    [purchase] = _reduce(events, origin=SourceOrigin.COMPLETED_MATCH, catalog_items=_CATALOG)
    assert purchase.retrospective_status == RETROSPECTIVE_REVERTED_BY_UNDO
    assert purchase.retrospective_correlation_unvalidated is True
    state = _decision_state_from_purchase(
        MATCH_ID, CATALOG_ID, SourceOrigin.COMPLETED_MATCH, purchase
    )
    assert state.quality["retrospectiveTarget"]["correlationUnvalidated"] is True


def test_uncorrelated_undo_makes_most_recent_completed_purchase_uncertain() -> None:
    events = [
        _event(0, 1000, "ITEM_PURCHASED", participantId=P1, itemId=1001),
        _event(1, 2000, "ITEM_PURCHASED", participantId=P1, itemId=1002),
        _event(2, 2100, "ITEM_UNDO", participantId=P1, beforeId=9999, afterId=0),
    ]
    purchases = _reduce(events, catalog_items=_CATALOG)
    assert _statuses(purchases) == [RETROSPECTIVE_COMPLETED, RETROSPECTIVE_STATUS_UNCERTAIN]
    assert purchases[1].retrospective_undo_source_index == 2


def test_second_uncorrelated_undo_taints_the_next_completed_purchase() -> None:
    events = [
        _event(0, 1000, "ITEM_PURCHASED", participantId=P1, itemId=1001),
        _event(1, 2000, "ITEM_PURCHASED", participantId=P1, itemId=1002),
        _event(2, 2100, "ITEM_UNDO", participantId=P1, beforeId=9999, afterId=0),
        _event(3, 2200, "ITEM_UNDO", participantId=P1, beforeId=8888, afterId=0),
    ]
    purchases = _reduce(events, catalog_items=_CATALOG)
    assert _statuses(purchases) == [RETROSPECTIVE_STATUS_UNCERTAIN, RETROSPECTIVE_STATUS_UNCERTAIN]
    assert [p.retrospective_undo_source_index for p in purchases] == [3, 2]


def test_uncorrelated_undo_never_overrides_an_already_reverted_purchase() -> None:
    events = [
        _event(0, 1000, "ITEM_PURCHASED", participantId=P1, itemId=1001),
        _event(1, 2000, "ITEM_PURCHASED", participantId=P1, itemId=1002),
        _event(2, 2100, "ITEM_UNDO", participantId=P1, beforeId=1002, afterId=0),
        _event(3, 2200, "ITEM_UNDO", participantId=P1, beforeId=9999, afterId=0),
    ]
    purchases = _reduce(events, catalog_items=_CATALOG)
    assert _statuses(purchases) == [RETROSPECTIVE_STATUS_UNCERTAIN, RETROSPECTIVE_REVERTED_BY_UNDO]
    assert purchases[1].retrospective_undo_source_index == 2  # nunca sobrescrito


def test_undo_of_a_sale_does_not_change_any_purchase_status() -> None:
    events = [
        _event(0, 1000, "ITEM_PURCHASED", participantId=P1, itemId=1001),
        _event(1, 2000, "ITEM_SOLD", participantId=P1, itemId=1001),
        _event(2, 2100, "ITEM_UNDO", participantId=P1, beforeId=0, afterId=1001),
    ]
    purchases = _reduce(events, catalog_items=_CATALOG)
    assert _statuses(purchases) == [RETROSPECTIVE_COMPLETED]


def test_retrospective_block_never_changes_pre_decision_or_cumulative_fields() -> None:
    """Mesmo prefixo, futuros com e sem undo: TODOS os campos anteriores à
    decisão da compra-alvo são byte-idênticos; só o bloco retrospectivo
    diverge (e apenas nas compras já processadas antes do undo)."""
    frames = [_Frame(0, {P1: {"currentGold": 500, "level": 2}})]
    prefix = [
        _event(0, 1000, "ITEM_PURCHASED", participantId=P1, itemId=1001),
        _event(1, 2000, "ITEM_PURCHASED", participantId=P1, itemId=1002),
    ]
    with_undo = [*prefix, _event(2, 2100, "ITEM_UNDO", participantId=P1, beforeId=1002, afterId=0)]
    with_uncorrelated = [
        *prefix,
        _event(2, 2100, "ITEM_UNDO", participantId=P1, beforeId=9999, afterId=0),
    ]
    baseline = _reduce(prefix, frames=frames, catalog_items=_CATALOG)
    reverted = _reduce(with_undo, frames=frames, catalog_items=_CATALOG)
    uncertain = _reduce(with_uncorrelated, frames=frames, catalog_items=_CATALOG)

    pre_decision_fields = (
        "inventory_before",
        "gold_estimate",
        "level_observation",
        "event_timestamp_ms",
        "pre_decision_uncertain",
        "pre_decision_reasons",
        "purchased_item_id",
        "participant_id",
    )
    for variant in (reverted, uncertain):
        for before, after in zip(baseline, variant, strict=True):
            for name in pre_decision_fields:
                assert getattr(before, name) == getattr(after, name), name
            # cumulativos da própria compra tampouco mudam: o undo é posterior
            assert (before.uncertain, before.reasons) == (after.uncertain, after.reasons)
    assert baseline[1].retrospective_status == RETROSPECTIVE_COMPLETED
    assert reverted[1].retrospective_status == RETROSPECTIVE_REVERTED_BY_UNDO
    assert uncertain[1].retrospective_status == RETROSPECTIVE_STATUS_UNCERTAIN


def test_persisted_pre_decision_payload_is_byte_identical_across_futures() -> None:
    """Mesma propriedade, no payload persistido: snapshot (sem o alvo) e
    preDecisionQuality/goldEstimate/levelObservation idênticos."""
    frames = [_Frame(0, {P1: {"currentGold": 500, "level": 2}})]
    prefix = [_event(0, 1000, "ITEM_PURCHASED", participantId=P1, itemId=1001)]
    future = [*prefix, _event(1, 1100, "ITEM_UNDO", participantId=P1, beforeId=1001, afterId=0)]
    [plain] = _reduce(prefix, frames=frames, catalog_items=_CATALOG)
    [with_undo] = _reduce(future, frames=frames, catalog_items=_CATALOG)
    origin = SourceOrigin.CURATED_DEMO
    a = _decision_state_from_purchase(MATCH_ID, CATALOG_ID, origin, plain)
    b = _decision_state_from_purchase(MATCH_ID, CATALOG_ID, origin, with_undo)
    for key in ("preDecisionQuality", "goldEstimate", "levelObservation"):
        assert a.quality[key] == b.quality[key], key
    assert a.snapshot == b.snapshot
    assert a.quality["retrospectiveTarget"] != b.quality["retrospectiveTarget"]
