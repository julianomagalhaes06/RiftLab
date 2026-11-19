"""CC-1501: seleção v3 — contexto do item principal na âncora de compromisso."""

from __future__ import annotations

from test_evaluation import (
    CATALOG_ROW,
    MATCH,
    PARTICIPANTS,
    TABLE,
    _buy,
    _scope,
    _states,
)

from riftlab.decisions.anchoring import anchor_points
from riftlab.decisions.selection import evaluate_decision


def _points(events, scope):
    states = _states(events)
    points = [
        evaluate_decision(
            s,
            participants=PARTICIPANTS,
            match=MATCH,
            catalog=CATALOG_ROW,
            classification=TABLE,
            scope=scope,
        )
        for s in states
    ]
    return states, anchor_points(
        states,
        points,
        participants=PARTICIPANTS,
        match=MATCH,
        classification=TABLE,
        scope=scope,
    )


def test_first_main_item_keeps_the_purchase_instant_and_the_second_uses_the_anchor():
    # 1º item: 1001 + 1002 -> 3001. Depois 1001 (componente do 3002) e 3002.
    events = [
        _buy(0, 60_000, 1001),
        _buy(1, 61_000, 1002),
        _buy(2, 62_000, 3001),
        _buy(3, 300_000, 1001),
        _buy(4, 420_000, 3002),
    ]
    states, out = _points(events, _scope())
    first, second = [(p, a) for p, a in out]
    assert first[1] is None and first[0].main_item_ordinal == 1
    point, anchor = second
    assert anchor is not None and anchor.event_source_index == 3  # 1ª compra depois do 3001
    assert anchor.timestamp_ms == 300_000 and point.event_source_index == 4
    assert point.eligible and point.main_item_ordinal == 2
    # Na âncora o componente do 3002 ainda não estava no inventário.
    assert point.features is not None
    assert point.features.values["inventory_items"] == [[3001, 1]]


def test_late_scope_ignores_champion_and_role_only_from_the_late_ordinal():
    events = [
        _buy(0, 60_000, 3002),
        _buy(1, 200_000, 3003),
        _buy(2, 400_000, 1001),
        _buy(3, 500_000, 3001),
    ]
    other_champion = _scope(champion_ids=frozenset({999}), late_from_ordinal=3)
    _, out = _points(events, other_champion)
    ordinals = {p.main_item_ordinal: p for p, _ in out}
    assert not ordinals[2].eligible  # 2º item: o campeão está fora do recorte
    assert any(r.code == "OUT_OF_SCOPE" for r in ordinals[2].reasons)
    assert ordinals[3].eligible and ordinals[3].reasons == ()  # 3º item: qualquer campeão
