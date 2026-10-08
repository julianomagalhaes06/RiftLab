"""Regras R7b/R7m/R7x (itens concedidos por runa) e R8 (missão de rota e botas), CC-324.

Desligadas, o reducer é idêntico ao anterior (a regressão bit a bit continua nos testes
da v7). Ligadas, só a forma observada na exploração pré-registrada do CC-324 deixa de
marcar incerteza; as demais continuam marcando. Sequências sintéticas, sem dado real.
"""

from __future__ import annotations

from typing import Any

from riftlab.shared.domain import SourceOrigin
from riftlab.timeline.application import ReconstructedPurchase, reconstruct_participant_purchases
from riftlab.timeline.domain import TimelineEvent
from riftlab.timeline.empirical_rules import (
    RULE_R4S,
    RULE_R7B,
    RULE_R7M,
    RULE_R7X,
    RULE_R8,
    RULE_R8T,
)
from support.reducer_scenarios import (
    BOOTS,
    BOOTS2,
    CATALOG,
    CID,
    FRAMES,
    P1,
    QUEST,
    SYSTEM_IDS,
    A,
    _item,
    buy,
    destroy,
)

REAL = SourceOrigin.COMPLETED_MATCH
BISCUIT, FOOTWEAR, AVARICE, FORCE, SKILL = 2010, 2422, 2151, 2152, 2150
MID_QUEST, TIER3 = 1201, 3175
R8_CATALOG = {
    **CATALOG,
    MID_QUEST: _item(MID_QUEST),
    TIER3: _item(TIER3, (BOOTS2,)),
    BISCUIT: _item(BISCUIT, tags=("Consumable",), consumed=True),
    FOOTWEAR: _item(FOOTWEAR, tags=("Boots",)),
    AVARICE: _item(AVARICE, tags=("Consumable",), consumed=True),
    FORCE: _item(FORCE, tags=("Consumable",), consumed=True),
    SKILL: _item(SKILL, tags=("Consumable",), consumed=True),
}


def _reduce(
    events: list[TimelineEvent], rules: frozenset[str], catalog: dict[int, Any] = R8_CATALOG
) -> list[ReconstructedPurchase]:
    return reconstruct_participant_purchases(
        participant_id=P1,
        events=events,
        origin=REAL,
        frames=FRAMES,
        catalog_id=CID,
        catalog_items=catalog,
        enabled_rules=rules | {RULE_R4S},
        system_item_ids=SYSTEM_IDS | {MID_QUEST},
    )


def _codes(purchase: ReconstructedPurchase) -> set[str]:
    return {reason.code for reason in purchase.pre_decision_reasons}


def test_granted_item_destroy_without_ownership_is_a_no_op_only_with_its_rule() -> None:
    cases = [
        (BISCUIT, RULE_R7B),
        (FOOTWEAR, RULE_R7M),
        (AVARICE, RULE_R7X),
        (FORCE, RULE_R7X),
        (SKILL, RULE_R7X),
    ]
    for item, rule in cases:
        events = [destroy(0, 30_000, item), buy(1, 60_000, A)]
        off = _reduce(events, frozenset())[-1]
        on = _reduce(events, frozenset({rule}))[-1]
        other = _reduce(events, frozenset({RULE_R7B, RULE_R7M, RULE_R7X} - {rule}))[-1]
        assert "DESTROY_WITHOUT_OWNERSHIP" in _codes(off), item
        assert not on.pre_decision_uncertain and on.inventory_before.items == (), item
        assert "DESTROY_WITHOUT_OWNERSHIP" in _codes(other), item


def test_owned_granted_item_follows_the_normal_consumption_path() -> None:
    events = [buy(0, 1_000, BISCUIT), destroy(1, 30_000, BISCUIT), buy(2, 60_000, A)]
    target = _reduce(events, frozenset({RULE_R7B}))[-1]

    assert "UNRECONCILED_ITEM_DESTROYED" in _codes(target)  # posse por compra, sem R1


def test_footwear_destroyed_with_a_tier2_purchase_leaves_only_the_new_boots() -> None:
    events = [destroy(0, 60_000, FOOTWEAR), buy(1, 60_000, BOOTS2), buy(2, 90_000, A)]
    target = _reduce(events, frozenset({RULE_R7M}))[-1]

    assert not target.pre_decision_uncertain
    assert target.inventory_before.items == ((BOOTS2, 1),)


def test_bot_lane_quest_removes_owned_boots_without_marking() -> None:
    for boots, rule, other in ((BOOTS, RULE_R8T, RULE_R8), (BOOTS2, RULE_R8, RULE_R8T)):
        events = [
            buy(0, 1_000, boots),
            destroy(1, 600_000, QUEST),
            destroy(2, 600_000, boots),
            buy(3, 700_000, A),
        ]
        off = _reduce(events, frozenset())[-1]
        on = _reduce(events, frozenset({rule}))[-1]
        wrong = _reduce(events, frozenset({other}))[-1]
        assert "UNRECONCILED_ITEM_DESTROYED" in _codes(off)
        assert "UNRECONCILED_ITEM_DESTROYED" in _codes(wrong)
        assert not on.pre_decision_uncertain and on.inventory_before.items == ()


def test_mid_lane_quest_upgrades_tier2_boots_to_the_unique_successor() -> None:
    events = [
        buy(0, 1_000, BOOTS2),
        destroy(1, 600_000, BOOTS2),
        destroy(2, 600_000, MID_QUEST),
        buy(3, 700_000, A),
    ]
    target = _reduce(events, frozenset({RULE_R8}))[-1]

    assert not target.pre_decision_uncertain
    assert target.inventory_before.items == ((TIER3, 1),)


def test_r8_keeps_marking_outside_the_observed_form() -> None:
    no_quest = [buy(0, 1_000, BOOTS2), destroy(1, 600_000, BOOTS2), buy(2, 700_000, A)]
    other_time = [
        buy(0, 1_000, BOOTS2),
        destroy(1, 600_000, MID_QUEST),
        destroy(2, 601_000, BOOTS2),
        buy(3, 700_000, A),
    ]
    not_boots = [
        buy(0, 1_000, A),
        destroy(1, 600_000, QUEST),
        destroy(2, 600_000, A),
        buy(3, 700_000, A),
    ]
    mid_on_tier1 = [
        buy(0, 1_000, BOOTS),
        destroy(1, 600_000, MID_QUEST),
        destroy(2, 600_000, BOOTS),
        buy(3, 700_000, A),
    ]
    ambiguous = {**R8_CATALOG, 3176: _item(3176, (BOOTS2,))}
    mid_two_successors = [
        buy(0, 1_000, BOOTS2),
        destroy(1, 600_000, MID_QUEST),
        destroy(2, 600_000, BOOTS2),
        buy(3, 700_000, A),
    ]
    for events in (no_quest, other_time, not_boots, mid_on_tier1):
        assert "UNRECONCILED_ITEM_DESTROYED" in _codes(_reduce(events, frozenset({RULE_R8}))[-1])
    target = _reduce(mid_two_successors, frozenset({RULE_R8}), ambiguous)[-1]
    assert "UNRECONCILED_ITEM_DESTROYED" in _codes(target)
