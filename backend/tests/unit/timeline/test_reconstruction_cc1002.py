"""Regras candidatas da CC-1002 (R1e, R6v, R7h, R8b, R8m, R9, R10j, R11s, R11f, R12w).

Desligadas, o reducer é idêntico ao anterior (a regressão bit a bit continua nos testes da
v7). Ligadas, só a forma observada na exploração do treino do 16.19 deixa de marcar
incerteza; as formas vizinhas continuam marcando. Sequências sintéticas, sem dado real.
"""

from __future__ import annotations

from typing import Any

from riftlab.shared.domain import SourceOrigin
from riftlab.timeline.application import (
    ReconstructedPurchase,
    reconstruct_participant_purchases,
)
from riftlab.timeline.domain import TimelineEvent
from riftlab.timeline.empirical_rules import (
    RULE_R1,
    RULE_R1E,
    RULE_R2,
    RULE_R4S,
    RULE_R4W,
    RULE_R5,
    RULE_R6V,
    RULE_R7H,
    RULE_R8B,
    RULE_R8M,
    RULE_R8T,
    RULE_R9,
    RULE_R10J,
    RULE_R11F,
    RULE_R11S,
    RULE_R12W,
    apply_role_prepasses,
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
    TRK_A,
    TRK_C,
    WARD,
    A,
    B,
    _item,
    buy,
    destroy,
    sell,
    undo,
)

REAL = SourceOrigin.COMPLETED_MATCH
ELIXIR, PET, JUNGLE_QUEST, MID_QUEST, TIER3 = 2140, 1101, 1204, 1201, 3175
ATLAS, COMPASS, BOUNTY, SLEIGH = 3865, 3866, 3867, 3876
SUPPORT_QUEST, SUPPORT_REWARD = 1203, 1208
ARCHANGEL, SERAPH, TEAR = 3003, 3040, 3070
EYE = 3513
CATALOG_1002: dict[int, Any] = {
    **CATALOG,
    ELIXIR: _item(ELIXIR, tags=("Consumable",), consumed=True),
    PET: _item(PET, tags=("Jungle",)),
    JUNGLE_QUEST: _item(JUNGLE_QUEST),
    MID_QUEST: _item(MID_QUEST),
    TIER3: _item(TIER3, (BOOTS2,)),
    ATLAS: _item(ATLAS),
    COMPASS: _item(COMPASS),
    BOUNTY: _item(BOUNTY),
    SUPPORT_QUEST: _item(SUPPORT_QUEST),
    SUPPORT_REWARD: _item(SUPPORT_REWARD),
    TEAR: _item(TEAR),
    ARCHANGEL: _item(ARCHANGEL, (TEAR,)),
    EYE: _item(EYE, tags=("Consumable",), consumed=True),
}
BASE = frozenset({RULE_R1, RULE_R2, RULE_R4S, RULE_R5})


def _purchases(
    events: list[TimelineEvent], rules: frozenset[str], *, support: bool = False
) -> list[ReconstructedPurchase]:
    rules = rules | BASE
    events = apply_role_prepasses(events, rules, [P1] if support else [])
    return reconstruct_participant_purchases(
        participant_id=P1,
        events=events,
        origin=REAL,
        frames=FRAMES,
        catalog_id=CID,
        catalog_items=CATALOG_1002,
        enabled_rules=rules,
        system_item_ids=SYSTEM_IDS | {MID_QUEST, JUNGLE_QUEST, SUPPORT_QUEST, SUPPORT_REWARD},
    )


def _last(
    events: list[TimelineEvent], rules: frozenset[str], **kwargs: Any
) -> ReconstructedPurchase:
    return _purchases(events, rules, **kwargs)[-1]


def _codes(purchase: ReconstructedPurchase) -> set[str]:
    return {reason.code for reason in purchase.pre_decision_reasons}


def test_r1e_consumable_destroyed_before_its_own_purchase_never_enters_the_inventory() -> None:
    events = [destroy(0, 60_000, ELIXIR), buy(1, 60_000, ELIXIR), buy(2, 90_000, A)]
    off = _last(events, frozenset())
    on = _last(events, frozenset({RULE_R1E}))
    assert "DESTROY_WITHOUT_OWNERSHIP" in _codes(off)
    assert not on.pre_decision_uncertain and on.inventory_before.items == ()


def test_r1e_needs_the_same_item_bought_after_the_destroy_in_the_same_instant() -> None:
    before = [buy(0, 60_000, ELIXIR), destroy(1, 60_000, ELIXIR), buy(2, 90_000, A)]
    other_instant = [destroy(0, 60_000, ELIXIR), buy(1, 61_000, ELIXIR), buy(2, 90_000, A)]
    # Com posse, segue o caminho do R1 (o elixir sem pilha declarada marca por outro motivo).
    assert "DESTROY_WITHOUT_OWNERSHIP" not in _codes(_last(before, frozenset({RULE_R1E})))
    assert "DESTROY_WITHOUT_OWNERSHIP" in _codes(_last(other_instant, frozenset({RULE_R1E})))


def test_r6v_correlated_undo_restores_the_inventory_without_marking() -> None:
    events = [buy(0, 1_000, A), buy(1, 2_000, B), undo(2, 3_000, B, 0), buy(3, 9_000, A)]
    off = _last(events, frozenset())
    on = _last(events, frozenset({RULE_R6V}))
    assert "UNVALIDATED_UNDO_SEMANTICS" in _codes(off)
    assert not on.pre_decision_uncertain and on.inventory_before.items == ((A, 1),)


def test_r6v_stacked_undos_and_uncorrelated_undo() -> None:
    stacked = [
        buy(0, 1_000, A),
        buy(1, 2_000, B),
        undo(2, 3_000, B, 0),
        undo(3, 3_500, A, 0),
        buy(4, 9_000, B),
    ]
    target = _last(stacked, frozenset({RULE_R6V}))
    assert not target.pre_decision_uncertain and target.inventory_before.items == ()

    uncorrelated = [buy(0, 1_000, A), undo(1, 2_000, B, 0), buy(2, 9_000, B)]
    assert "UNDO_UNCORRELATED" in _codes(_last(uncorrelated, frozenset({RULE_R6V})))


def test_r10j_pet_leaves_when_the_jungle_quest_ends() -> None:
    events = [
        buy(0, 1_000, PET),
        destroy(1, 840_000, PET),
        destroy(2, 840_000, JUNGLE_QUEST),
        buy(3, 900_000, A),
    ]
    off = _last(events, frozenset())
    on = _last(events, frozenset({RULE_R10J}))
    assert "UNRECONCILED_ITEM_DESTROYED" in _codes(off)
    assert not on.pre_decision_uncertain and on.inventory_before.items == ()

    without_quest = [buy(0, 1_000, PET), destroy(1, 840_000, PET), buy(2, 900_000, A)]
    assert "UNRECONCILED_ITEM_DESTROYED" in _codes(_last(without_quest, frozenset({RULE_R10J})))


def test_r8m_tier2_bought_after_the_mid_quest_enters_as_its_successor() -> None:
    events = [
        destroy(0, 600_000, MID_QUEST),
        buy(1, 650_000, BOOTS),
        destroy(2, 700_000, BOOTS),
        destroy(3, 700_000, BOOTS2),
        buy(4, 700_000, BOOTS2),
        buy(5, 800_000, A),
    ]
    off = _last(events, frozenset())
    on = _last(events, frozenset({RULE_R8M}))
    assert "DESTROY_WITHOUT_OWNERSHIP" in _codes(off)
    assert not on.pre_decision_uncertain and on.inventory_before.items == ((TIER3, 1),)

    before_quest = [
        buy(0, 650_000, BOOTS),
        destroy(1, 700_000, BOOTS),
        destroy(2, 700_000, BOOTS2),
        buy(3, 700_000, BOOTS2),
        destroy(4, 750_000, MID_QUEST),
        buy(5, 800_000, A),
    ]
    assert "DESTROY_WITHOUT_OWNERSHIP" in _codes(_last(before_quest, frozenset({RULE_R8M})))


def test_r8b_boots_live_in_the_role_slot_after_the_bot_quest() -> None:
    events = [
        buy(0, 1_000, BOOTS),
        destroy(1, 600_000, QUEST),
        destroy(2, 600_000, BOOTS),
        destroy(3, 700_000, BOOTS),
        buy(4, 700_000, BOOTS2),
        buy(5, 800_000, A),
    ]
    off = _last(events, frozenset({RULE_R8T}))
    on = _last(events, frozenset({RULE_R8T, RULE_R8B}))
    assert "DESTROY_WITHOUT_OWNERSHIP" in _codes(off)
    assert not on.pre_decision_uncertain and on.inventory_before.items == ()


def test_r11s_and_r11f_follow_the_support_item_chain() -> None:
    chain = [
        buy(0, 0, ATLAS),
        destroy(1, 400_000, ATLAS),
        destroy(2, 700_000, COMPASS),
        destroy(3, 700_000, SUPPORT_QUEST),
        buy(4, 750_000, A),
    ]
    off = _last(chain, frozenset())
    on = _last(chain, frozenset({RULE_R11S}))
    assert "UNRECONCILED_ITEM_DESTROYED" in _codes(off)
    assert not on.pre_decision_uncertain and on.inventory_before.items == ((BOUNTY, 1),)

    unobserved_choice = [*chain[:4], destroy(4, 720_000, BOUNTY), buy(5, 750_000, A)]
    only_chain = _last(unobserved_choice, frozenset({RULE_R11S}))
    with_final = _last(unobserved_choice, frozenset({RULE_R11S, RULE_R11F}))
    assert "UNRECONCILED_ITEM_DESTROYED" in _codes(only_chain)
    assert not with_final.pre_decision_uncertain
    assert with_final.inventory_before.items == ((BOUNTY, 1),)

    observed_choice = [
        *chain[:4],
        destroy(4, 720_000, BOUNTY),
        buy(5, 720_000, SLEIGH),
        buy(6, 750_000, A),
    ]
    target = _last(observed_choice, frozenset({RULE_R11S, RULE_R11F}))
    assert not target.pre_decision_uncertain and target.inventory_before.items == ((BOUNTY, 1),)

    # A escolha final vendida depois é lida pela Recompensa (linhagem).
    sold = [*unobserved_choice[:5], sell(5, 900_000, SLEIGH), buy(6, 950_000, A)]
    after_sale = _last(sold, frozenset({RULE_R11S, RULE_R11F}))
    assert not after_sale.pre_decision_uncertain and after_sale.inventory_before.items == ()


def test_r12w_support_wards_never_touch_the_normal_inventory() -> None:
    events = [
        destroy(0, 200_000, WARD),
        destroy(1, 200_000, SUPPORT_QUEST),
        buy(2, 200_000, WARD),
        buy(3, 300_000, WARD),
        buy(4, 400_000, A),
    ]
    off = _last(events, frozenset(), support=True)
    on = _last(events, frozenset({RULE_R12W}), support=True)
    not_support = _last(events, frozenset({RULE_R12W}), support=False)
    assert "DESTROY_WITHOUT_OWNERSHIP" in _codes(off)
    assert not on.pre_decision_uncertain and on.inventory_before.items == ()
    assert "DESTROY_WITHOUT_OWNERSHIP" in _codes(not_support)


def test_r12w_prepass_marks_only_support_wards() -> None:
    events = [buy(0, 1_000, WARD), buy(1, 1_000, A), buy(2, 1_000, WARD, p=6)]
    marked = apply_role_prepasses(events, frozenset({RULE_R12W}), [P1])
    assert [bool(e.payload.get("roleSlot")) for e in marked] == [True, False, False]
    assert apply_role_prepasses(events, frozenset({RULE_R4W}), [P1]) == events


def test_r9_transformation_keeps_the_origin_and_reads_the_result_as_it() -> None:
    events = [
        buy(0, 1_000, TEAR),
        destroy(1, 900_000, TEAR),
        buy(2, 900_000, ARCHANGEL),
        destroy(3, 1_200_000, ARCHANGEL),
        buy(4, 1_300_000, A),
    ]
    off = _last(events, frozenset())
    on = _last(events, frozenset({RULE_R9}))
    assert "UNRECONCILED_ITEM_DESTROYED" in _codes(off)
    assert not on.pre_decision_uncertain and on.inventory_before.items == ((ARCHANGEL, 1),)

    sold = [*events[:4], sell(4, 1_250_000, SERAPH), buy(5, 1_300_000, A)]
    after_sale = _last(sold, frozenset({RULE_R9}))
    assert not after_sale.pre_decision_uncertain and after_sale.inventory_before.items == ()


def test_r9_destroy_claimed_by_a_later_purchase_in_the_same_instant_is_a_recipe() -> None:
    catalog_parent = {**CATALOG_1002, 9999: _item(9999, (ARCHANGEL,))}
    events = [
        buy(0, 1_000, TEAR),
        destroy(1, 900_000, TEAR),
        buy(2, 900_000, ARCHANGEL),
        destroy(3, 1_200_000, ARCHANGEL),
        buy(4, 1_200_000, 9999),
        buy(5, 1_300_000, A),
    ]
    target = reconstruct_participant_purchases(
        participant_id=P1,
        events=events,
        origin=REAL,
        frames=FRAMES,
        catalog_id=CID,
        catalog_items=catalog_parent,
        enabled_rules=BASE | {RULE_R9},
        system_item_ids=SYSTEM_IDS,
    )[-1]
    assert not target.pre_decision_uncertain and target.inventory_before.items == ((9999, 1),)


def test_r7h_herald_eye_borrows_the_trinket_slot_until_used() -> None:
    events = [
        buy(0, 1_000, TRK_C),
        destroy(1, 600_000, TRK_C),
        destroy(2, 650_000, EYE),
        buy(3, 700_000, A),
    ]
    off = _last(events, frozenset({RULE_R2}))
    on = _last(events, frozenset({RULE_R2, RULE_R7H}))
    assert "UNRECONCILED_ITEM_DESTROYED" in _codes(off)
    assert not on.pre_decision_uncertain and on.inventory_before.trinket_item_id == TRK_C

    swap_next = [
        buy(0, 1_000, TRK_C),
        destroy(1, 600_000, TRK_C),
        buy(2, 650_000, TRK_A),
        buy(3, 700_000, A),
    ]
    assert "UNRECONCILED_ITEM_DESTROYED" in _codes(_last(swap_next, frozenset({RULE_R7H})))
