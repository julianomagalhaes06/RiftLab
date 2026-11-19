"""CC-1501: classificação da compra no instante em que fica pronta e âncora de compromisso."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from riftlab.catalog.domain import Item
from riftlab.decisions.commitment import (
    CompletionKind,
    assess_completion,
    commitment_anchor,
    path_start,
    previous_main_index,
    recipe_closure,
)
from riftlab.timeline.application import RECONSTRUCTION_ALGORITHM_VERSION
from riftlab.timeline.domain import DecisionState

CATALOG = uuid.UUID(int=7)


@dataclass(frozen=True)
class Cand:
    item_id: int
    remaining_cost: int


def item(item_id: int, price: int, recipe: tuple[int, ...] = ()) -> Item:
    return Item(
        CATALOG,
        item_id,
        price,
        [{"itemId": c, "quantity": recipe.count(c)} for c in sorted(set(recipe))],
        {"name": str(item_id), "tags": [], "stats": {}, "into": []},
        {"purchasable": True, "maps": [11]},
    )


def state(index: int, purchased: int, ts: int | None = None) -> DecisionState:
    return DecisionState(
        id=uuid.UUID(int=index + 1),
        match_id="M",
        participant_id=1,
        event_source_index=index,
        state_version=RECONSTRUCTION_ALGORITHM_VERSION,
        snapshot={
            "inventory": {"items": {}},
            "purchasedItemId": purchased,
            "eventTimestampMs": ts if ts is not None else index * 1000,
        },
        features={},
        quality={},
    )


PRICES = {10: 3000, 20: 3000, 30: 2800}


def test_forced_when_only_the_target_reuses_the_paid_gold():
    result = assess_completion(10, [Cand(10, 700), Cand(20, 3000), Cand(30, 2500)], PRICES)
    assert result.kind is CompletionKind.FORCED
    assert result.paid_gold == 2300 and result.rivals == ()
    assert round(result.share_paid or 0, 3) == round(2300 / 3000, 3)


def test_shared_when_a_rival_reuses_at_least_the_same_gold():
    # Componente comum (ex.: B.F.): os dois aproveitam 1300 de ouro.
    result = assess_completion(10, [Cand(10, 1700), Cand(20, 1700), Cand(30, 2800)], PRICES)
    assert result.kind is CompletionKind.SHARED
    assert result.rivals == (20,)


def test_rival_that_reuses_more_also_counts():
    # O jogador tinha mais do item 20 pronto e mesmo assim terminou o 10.
    result = assess_completion(10, [Cand(10, 2000), Cand(20, 500)], PRICES)
    assert result.kind is CompletionKind.SHARED and result.rivals == (20,)


def test_open_when_nothing_was_paid_and_unknown_without_candidates():
    assert (
        assess_completion(10, [Cand(10, 3000), Cand(20, 3000)], PRICES).kind is CompletionKind.OPEN
    )
    assert assess_completion(10, None, PRICES).kind is CompletionKind.UNKNOWN
    assert assess_completion(10, [Cand(20, 3000)], PRICES).kind is CompletionKind.UNKNOWN


def test_anchor_is_first_purchase_after_previous_main_never_after_target():
    states = [state(i, p) for i, p in [(1, 1055), (5, 20), (7, 1036), (9, 1037), (12, 10)]]
    main = {20, 10}
    prior = previous_main_index(states, 12, lambda s: s.snapshot["purchasedItemId"] in main)
    assert prior == 5
    anchor = commitment_anchor(states, target_index=12, previous_main=prior)
    assert anchor is not None and anchor.event_source_index == 7
    # Primeiro item principal: a primeira compra da partida.
    first = commitment_anchor(states, target_index=5, previous_main=None)
    assert first is not None and first.event_source_index == 1


def test_anchor_can_be_the_target_itself_when_bought_outright():
    states = [state(5, 20), state(12, 10)]
    anchor = commitment_anchor(states, target_index=12, previous_main=5)
    assert anchor is not None and anchor.event_source_index == 12


def test_path_start_finds_first_component_of_the_target_tree():
    items = {10: item(10, 3000, (1037, 1036)), 1037: item(1037, 875), 1036: item(1036, 350)}
    assert recipe_closure(10, items) == frozenset({1036, 1037})
    states = [state(5, 20), state(7, 2055), state(9, 1037), state(12, 10)]
    start = path_start(states, target_index=12, target_item_id=10, previous_main=5, items=items)
    assert start is not None and start.event_source_index == 9
    # Componentes comprados antes do item anterior não contam como início do caminho.
    early = [state(2, 1037), state(5, 20), state(12, 10)]
    assert (
        path_start(early, target_index=12, target_item_id=10, previous_main=5, items=items) is None
    )
