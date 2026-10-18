"""Reconstrução v6 (CC-204): semântica de TRANSAÇÃO para a forma real
destroy→compra (defeito herdado do CC-203: o snapshot omitia a receita do
próprio alvo). Na forma real (851/851 no
corpus local), os ITEM_DESTROYED dos componentes vêm ANTES da compra composta,
no mesmo timestamp T; os que a compra reivindica fazem parte da transação dela.

S1 o ponto de decisão fica ANTES da transação (inventory_before inclui as
   unidades reivindicadas; pre_decision_* sem as marcas desses destroys);
S2 destroy reivindicado sai do cumulativo (DEMO: sem marca; REAL: só o gate
   UNVALIDATED_DESTROY_ORDER_RECONCILIATION, inalterado);
S3 destroy em T NÃO reivindicado continua conservador (com a marca);
S4 undo dessa compra restaura os componentes;
S5 a forma direta (compra antes dos destroys) não muda.
Puro, sem I/O."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

import pytest

from riftlab.catalog.domain import Item
from riftlab.shared.domain import SourceOrigin
from riftlab.timeline.application import (
    RECONSTRUCTION_ALGORITHM_VERSION,
    ReconstructedPurchase,
    _decision_state_from_purchase,
    reconstruct_participant_purchases,
)
from riftlab.timeline.domain import TimelineEvent

DEMO, REAL = SourceOrigin.CURATED_DEMO, SourceOrigin.COMPLETED_MATCH
CID = uuid.uuid4()
P1, P6 = 1, 6
A, B, IE, X, TRINKET, ONE_A = 1038, 1037, 3031, 2003, 3340, 3002


@dataclass(frozen=True, slots=True)
class _Frame:
    timestamp_ms: int
    participant_frames: dict[int, dict[str, Any]] = field(default_factory=dict)


def _item(item_id: int, recipe: tuple[int, ...] = (), tags: tuple[str, ...] = ()) -> Item:
    return Item(
        CID,
        item_id,
        100,
        [{"itemId": c, "quantity": 1} for c in recipe],
        {"tags": list(tags), "into": []},
        {},
    )


CATALOG = {
    A: _item(A),
    B: _item(B),
    IE: _item(IE, recipe=(A, B)),  # receita real: dois componentes distintos
    ONE_A: _item(ONE_A, recipe=(A,)),
    X: _item(X, tags=("Consumable",)),
    TRINKET: _item(TRINKET, tags=("Trinket",)),
}
FRAMES = [_Frame(0, {P1: {"currentGold": 700, "level": 3}, P6: {"currentGold": 400, "level": 2}})]


def _ev(index: int, ts: int, type_: str, participant: int = P1, **payload: Any) -> TimelineEvent:
    return TimelineEvent("M", index, ts, type_, {"participantId": participant, **payload})


def _buy(index: int, ts: int, item_id: int, participant: int = P1) -> TimelineEvent:
    return _ev(index, ts, "ITEM_PURCHASED", participant, itemId=item_id)


def _destroy(index: int, ts: int, item_id: int, participant: int = P1) -> TimelineEvent:
    return _ev(index, ts, "ITEM_DESTROYED", participant, itemId=item_id)


def _reduce(
    events: list[TimelineEvent],
    origin: SourceOrigin = DEMO,
    participant: int = P1,
    with_catalog: bool = True,
) -> list[ReconstructedPurchase]:
    return reconstruct_participant_purchases(
        participant_id=participant,
        events=events,
        origin=origin,
        frames=FRAMES,
        catalog_id=CID if with_catalog else None,
        catalog_items=CATALOG if with_catalog else {},
    )


HISTORY = [_buy(0, 1000, A), _buy(1, 2000, B)]
T = 60_000  # instante da transação (frame de 0 -> 60000 ms de idade, fresco)


def _real_form() -> list[TimelineEvent]:
    """Forma real: destroys dos componentes ANTES da compra composta, mesmo T."""
    return [*HISTORY, _destroy(2, T, A), _destroy(3, T, B), _buy(4, T, IE)]


def _codes(reasons: tuple[Any, ...]) -> list[str]:
    return [r.code for r in reasons]


# ---------------------------------------------------------------------------
# Invariante correto: mesmo histórico antes de T, três alternativas em T
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("origin", [DEMO, REAL])
def test_three_alternatives_at_T_give_identical_pre_decision(origin: SourceOrigin) -> None:
    variants = {
        "destroys+IE (forma real)": _real_form(),
        "IE sem destroys": [*HISTORY, _buy(2, T, IE)],
        "alvo sem relação": [*HISTORY, _buy(2, T, X)],
    }
    last = {name: _reduce(events, origin)[-1] for name, events in variants.items()}
    baseline = last["IE sem destroys"]
    assert dict(baseline.inventory_before.items) == {A: 1, B: 1}
    assert baseline.pre_decision_uncertain is False and baseline.pre_decision_reasons == ()
    for name, purchase in last.items():
        assert purchase.inventory_before == baseline.inventory_before, name
        assert purchase.pre_decision_uncertain == baseline.pre_decision_uncertain, name
        assert purchase.pre_decision_reasons == baseline.pre_decision_reasons, name
        assert purchase.gold_estimate == baseline.gold_estimate, name
        assert purchase.level_observation == baseline.level_observation, name
        assert purchase.event_timestamp_ms == baseline.event_timestamp_ms, name


# ---------------------------------------------------------------------------
# S1/S2: transação do alvo
# ---------------------------------------------------------------------------


def test_s2_demo_claimed_destroys_leave_no_mark_at_all() -> None:
    target = _reduce(_real_form(), DEMO)[-1]
    assert set(target.destroy_order_claimed_source_indexes) == {2, 3}
    assert target.reasons == () and target.uncertain is False
    assert target.destroy_order_reconciliation_used is True


def test_s2_real_keeps_only_the_unvalidated_order_gate() -> None:
    target = _reduce(_real_form(), REAL)[-1]
    assert _codes(target.reasons) == ["UNVALIDATED_DESTROY_ORDER_RECONCILIATION"]  # gate REAL igual
    assert target.uncertain is True  # afeta as decisões seguintes, não a própria
    assert target.pre_decision_uncertain is False  # a própria decisão é anterior à transação


@pytest.mark.parametrize("origin", [DEMO, REAL])
def test_later_decision_sees_the_composed_item_and_only_the_real_gate(origin: SourceOrigin) -> None:
    events = [*_real_form(), _buy(5, T + 5_000, X)]
    later = _reduce(events, origin)[-1]
    assert dict(later.inventory_before.items) == {IE: 1}
    assert "UNRECONCILED_ITEM_DESTROYED" not in _codes(later.pre_decision_reasons)
    if origin is DEMO:
        assert later.pre_decision_uncertain is False
    else:
        assert _codes(later.pre_decision_reasons) == ["UNVALIDATED_DESTROY_ORDER_RECONCILIATION"]


def test_persisted_state_of_the_real_form_is_clean_before_the_transaction() -> None:
    target = _reduce(_real_form(), REAL)[-1]
    state = _decision_state_from_purchase("M", CID, REAL, target)
    assert state.state_version == RECONSTRUCTION_ALGORITHM_VERSION == 11
    assert state.snapshot["inventory"]["items"] == {str(B): 1, str(A): 1}
    assert state.quality["preDecisionQuality"] == {"uncertain": False, "reasons": []}
    claimed = state.quality["destroyOrderReconciliationHeuristic"]["claimedDestroySourceIndexes"]
    assert sorted(claimed) == [2, 3]


# ---------------------------------------------------------------------------
# S3: destroy não reivindicado segue conservador
# ---------------------------------------------------------------------------


def test_s3_unrelated_purchase_between_destroy_and_claiming_purchase_stays_conservative() -> None:
    """destroy A @T, compra X sem relação @T, compra do item que reivindica A
    @T: o estado de X fica conservador (sem A, com a marca) e o do item que
    reivindica fica limpo (A volta ao snapshot)."""
    events = [_buy(0, 1000, A), _destroy(1, T, A), _buy(2, T, X), _buy(3, T, ONE_A)]
    _, unrelated, claiming = _reduce(events, DEMO)
    assert unrelated.inventory_before.owned(A) == 0  # conservador: sem a unidade
    assert unrelated.pre_decision_uncertain is True
    assert [(r.code, r.event_source_index) for r in unrelated.pre_decision_reasons] == [
        ("UNRECONCILED_ITEM_DESTROYED", 1)
    ]
    assert claiming.inventory_before.owned(A) == 1  # a unidade reivindicada volta
    assert claiming.inventory_before.owned(X) == 1  # e X (comprado antes) está lá
    assert claiming.pre_decision_uncertain is False and claiming.pre_decision_reasons == ()
    assert claiming.reasons == () and claiming.uncertain is False
    # o estado de X nunca é curado depois: idêntico ao cálculo sem a compra seguinte
    alone = _reduce(events[:3], DEMO)[1]
    for name in ("inventory_before", "pre_decision_uncertain", "pre_decision_reasons", "reasons"):
        assert getattr(alone, name) == getattr(unrelated, name), name


def test_s3_destroy_claimed_by_nobody_keeps_mark_and_removal() -> None:
    events = [_buy(0, 1000, A), _destroy(1, T, A), _buy(2, T, X), _buy(3, T + 1000, X)]
    _, same_t, later = _reduce(events, DEMO)
    assert same_t.pre_decision_uncertain and later.pre_decision_uncertain
    assert later.inventory_before.owned(A) == 0
    assert _codes(later.pre_decision_reasons) == ["UNRECONCILED_ITEM_DESTROYED"]


def test_two_equal_units_destroyed_only_one_claimed() -> None:
    """2x A destruídas em T, receita precisa de 1: reivindica a primeira (FIFO);
    a outra continua conservadora (removida e marcada)."""
    events = [_buy(0, 1000, A), _buy(1, 1001, A), _destroy(2, T, A), _destroy(3, T, A)]
    events.append(_buy(4, T, ONE_A))
    claiming = _reduce(events, DEMO)[-1]
    assert claiming.destroy_order_claimed_source_indexes == (2,)
    assert claiming.inventory_before.owned(A) == 1  # só a unidade reivindicada volta
    assert [(r.code, r.event_source_index) for r in claiming.pre_decision_reasons] == [
        ("UNRECONCILED_ITEM_DESTROYED", 3)  # a marca da unidade NÃO reivindicada permanece
    ]
    assert [(r.code, r.event_source_index) for r in claiming.reasons] == [
        ("UNRECONCILED_ITEM_DESTROYED", 3)
    ]


def test_destroy_of_trinket_or_unowned_item_is_never_claimed() -> None:
    events = [
        _buy(0, 1000, TRINKET),
        _destroy(1, T, TRINKET),  # trinket: nunca entra no pool
        _destroy(2, T, A),  # sem posse: DESTROY_WITHOUT_OWNERSHIP, nunca entra no pool
        _buy(3, T, ONE_A),
    ]
    target = _reduce(events, DEMO)[-1]
    assert target.destroy_order_claimed_source_indexes == ()
    assert target.inventory_before.trinket_item_id is None  # remoção do trinket segue conservadora
    assert set(_codes(target.pre_decision_reasons)) == {
        "UNRECONCILED_ITEM_DESTROYED",
        "DESTROY_WITHOUT_OWNERSHIP",
    }
    assert target.pre_decision_uncertain is True


def test_without_catalog_nothing_is_claimed_and_no_catalog_is_kept() -> None:
    target = _reduce(_real_form(), DEMO, with_catalog=False)[-1]
    assert target.destroy_order_claimed_source_indexes == ()
    assert target.inventory_before.items == ()  # nada reivindicado: conservador
    assert _codes(target.pre_decision_reasons).count("UNRECONCILED_ITEM_DESTROYED") == 2
    assert "NO_CATALOG" in _codes(target.pre_decision_reasons)


# ---------------------------------------------------------------------------
# S4: undo restaura os componentes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("origin", [DEMO, REAL])
def test_s4_undo_of_the_claiming_purchase_restores_the_components(origin: SourceOrigin) -> None:
    events = [
        *_real_form(),
        _ev(5, T + 1000, "ITEM_UNDO", beforeId=IE, afterId=0),
        _buy(6, T + 2000, X),
    ]
    purchases = _reduce(events, origin)
    composed, after_undo = purchases[-2], purchases[-1]
    assert composed.retrospective_status == "REVERTED_BY_UNDO"
    assert dict(after_undo.inventory_before.items) == {A: 1, B: 1}  # componentes de volta
    assert after_undo.inventory_before.owned(IE) == 0
    assert "UNRECONCILED_ITEM_DESTROYED" not in _codes(after_undo.pre_decision_reasons)
    if origin is DEMO:
        assert after_undo.pre_decision_uncertain is False
    else:  # gates REAL inalterados: cada heurística aplicada marca incerto
        assert set(_codes(after_undo.pre_decision_reasons)) == {
            "UNVALIDATED_DESTROY_ORDER_RECONCILIATION",
            "UNVALIDATED_UNDO_SEMANTICS",
        }


# ---------------------------------------------------------------------------
# S5: forma direta (compra antes dos destroys) não muda
# ---------------------------------------------------------------------------


def test_s5_direct_form_is_unchanged() -> None:
    events = [*HISTORY, _buy(2, T, IE), _destroy(3, T, A), _destroy(4, T, B), _buy(5, T + 1, X)]
    compound, later = _reduce(events, DEMO)[-2:]
    assert dict(compound.inventory_before.items) == {A: 1, B: 1}
    assert compound.pre_decision_uncertain is False
    assert compound.destroy_order_claimed_source_indexes == ()  # nada reivindicado retroativamente
    assert later.pre_decision_uncertain is False  # destroys reconciliados pela compra anterior
    assert dict(later.inventory_before.items) == {IE: 1}


# ---------------------------------------------------------------------------
# Isolamento entre participantes no mesmo T
# ---------------------------------------------------------------------------


def test_two_participants_at_the_same_instant_are_isolated() -> None:
    p1 = _real_form()
    p6 = [
        _buy(10, 1500, A, P6),
        _destroy(11, T, A, P6),  # mesmo item e mesmo T do participante 1
        _buy(12, T, X, P6),
    ]
    both = sorted([*p1, *p6], key=lambda e: e.source_index)
    for participant, own in ((P1, p1), (P6, p6)):
        together = _reduce(both, DEMO, participant)
        alone = _reduce(own, DEMO, participant)
        assert together == alone  # o outro participante nunca interfere
    # o destroy do participante 6 nunca é reivindicado pela compra do participante 1
    assert set(_reduce(both, DEMO, P1)[-1].destroy_order_claimed_source_indexes) == {2, 3}
    # e o participante 6 (destroy sem compra que o reivindique) segue conservador
    assert _reduce(both, DEMO, P6)[-1].pre_decision_uncertain is True
