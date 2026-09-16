"""Reducer puro de reconstrução de inventário (CC-203):
reconstruct_participant_purchases. Sem I/O, sem Postgres, sem rede — testa
comportamento e falha do reducer em isolamento, não a persistência
(ver tests/integration/test_timeline_reconstruction.py para o orquestrador).

Corpus real local (data/raw/cc-105/) não está disponível neste ambiente
(BLOCKED, ver .private/validation/sprint-2/CC-203/) — sequências de
compra+destroy/empate temporal abaixo são sintéticas, construídas a partir
do schema confirmado pela auditoria CC-105 (beforeId/afterId presentes em
100% dos ITEM_UNDO reais; eventos repetidos com mesma assinatura ocorrem de
verdade), não de partidas reais observadas."""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from riftlab.catalog.domain import Item
from riftlab.timeline.application import reconstruct_participant_purchases
from riftlab.timeline.domain import TimelineEvent, TimelineReplayError

MATCH_ID = "MATCH-1"
CATALOG_ID = uuid.uuid4()
P1 = 1
P6 = 6


def _event(source_index: int, timestamp_ms: int, type_: str, **payload: Any) -> TimelineEvent:
    return TimelineEvent(
        match_id=MATCH_ID,
        source_index=source_index,
        timestamp_ms=timestamp_ms,
        type=type_,
        payload=payload,
    )


def _item(
    item_id: int, recipe: list[dict[str, Any]] | None = None, tags: tuple[str, ...] = ()
) -> Item:
    return Item(
        catalog_id=CATALOG_ID,
        item_id=item_id,
        price=100,
        recipe=recipe or [],
        attributes={"tags": list(tags)},
        flags={},
    )


def _reduce(
    events: list[TimelineEvent],
    *,
    participant_id: int = P1,
    catalog_id: uuid.UUID | None = CATALOG_ID,
    catalog_items: dict[int, Item] | None = None,
    excluded_item_ids: frozenset[int] = frozenset(),
):
    return reconstruct_participant_purchases(
        participant_id=participant_id,
        events=events,
        catalog_id=catalog_id,
        catalog_items=catalog_items,
        excluded_item_ids=excluded_item_ids,
    )


# ---------------------------------------------------------------------------
# Critérios de aceite do card (os 4 explícitos)
# ---------------------------------------------------------------------------


def test_purchase_target_never_appears_in_its_own_snapshot() -> None:
    events = [_event(0, 1000, "ITEM_PURCHASED", participantId=P1, itemId=1001)]
    [purchase] = _reduce(events, catalog_items={1001: _item(1001)})
    assert purchase.inventory_before.owned(1001) == 0
    assert purchase.purchased_item_id == 1001


def test_repeated_components_are_consumed_by_quantity() -> None:
    # Duas Adagas reais (achado do CC-202): recipe=[{"itemId":1042,"quantity":2}]
    events = [
        _event(0, 1000, "ITEM_PURCHASED", participantId=P1, itemId=1042),
        _event(1, 1001, "ITEM_PURCHASED", participantId=P1, itemId=1042),
        _event(2, 1002, "ITEM_PURCHASED", participantId=P1, itemId=3035),
    ]
    catalog = {
        1042: _item(1042),
        3035: _item(3035, recipe=[{"itemId": 1042, "quantity": 2}]),
    }
    purchases = _reduce(events, catalog_items=catalog)
    final_snapshot_before_next = purchases[2].inventory_before
    assert final_snapshot_before_next.owned(1042) == 2  # ambas presentes antes da compra final
    # não há mais compras depois; inferimos o pós-estado a partir da próxima compra sintética
    events.append(_event(3, 1003, "ITEM_PURCHASED", participantId=P1, itemId=9999))
    catalog[9999] = _item(9999)
    purchases = _reduce(events, catalog_items=catalog)
    assert purchases[3].inventory_before.owned(1042) == 0  # as duas consumidas, nunca sobra 1
    assert purchases[3].inventory_before.owned(3035) == 1


def test_undo_restores_proven_state_including_consumed_components() -> None:
    catalog = {
        1042: _item(1042),
        3035: _item(3035, recipe=[{"itemId": 1042, "quantity": 1}]),
    }
    events = [
        _event(0, 1000, "ITEM_PURCHASED", participantId=P1, itemId=1042),
        _event(1, 1001, "ITEM_PURCHASED", participantId=P1, itemId=3035),
        _event(2, 1002, "ITEM_UNDO", participantId=P1, beforeId=3035, afterId=0),
        _event(3, 1003, "ITEM_PURCHASED", participantId=P1, itemId=9999),
    ]
    catalog[9999] = _item(9999)
    purchases = _reduce(events, catalog_items=catalog)
    # ITEM_UNDO não produz decisão própria: purchases = [1042, 3035, 9999]
    # undo do item 3035 deve restaurar o componente 1042 consumido, não só remover 3035
    post_undo = purchases[2].inventory_before
    assert post_undo.owned(1042) == 1
    assert post_undo.owned(3035) == 0


def test_ambiguity_never_healed_by_a_later_normal_purchase() -> None:
    events = [
        _event(
            0, 1000, "ITEM_UNDO", participantId=P1, beforeId=1042, afterId=0
        ),  # sem operação prévia
        _event(1, 1001, "ITEM_PURCHASED", participantId=P1, itemId=9999),
    ]
    catalog = {9999: _item(9999)}
    [purchase] = _reduce(events, catalog_items=catalog)
    assert purchase.uncertain is True
    assert any(r.code == "UNDO_UNCORRELATED" for r in purchase.reasons)


# ---------------------------------------------------------------------------
# Casos comportamentais da validação pertinente (card + plano)
# ---------------------------------------------------------------------------


def test_direct_purchase_with_zero_components_owned() -> None:
    catalog = {
        1042: _item(1042),
        3035: _item(3035, recipe=[{"itemId": 1042, "quantity": 2}]),
    }
    events = [_event(0, 1000, "ITEM_PURCHASED", participantId=P1, itemId=3035)]
    [purchase] = _reduce(events, catalog_items=catalog)
    assert purchase.uncertain is False  # compra direta paga o restante, nunca fica incerta


def test_two_identical_units_of_a_base_item() -> None:
    catalog = {1042: _item(1042)}
    events = [
        _event(0, 1000, "ITEM_PURCHASED", participantId=P1, itemId=1042),
        _event(1, 1001, "ITEM_PURCHASED", participantId=P1, itemId=1042),
        _event(2, 1002, "ITEM_PURCHASED", participantId=P1, itemId=9999),
    ]
    catalog[9999] = _item(9999)
    purchases = _reduce(events, catalog_items=catalog)
    assert purchases[2].inventory_before.owned(1042) == 2


def test_recipe_chain_does_not_double_count_shared_component() -> None:
    # A (base) -> B (recipe=[A x1]) -> C (recipe=[B x1, A x1]); comprar 2x A,
    # depois B (consome 1 A), depois C (consome B + 1 A restante).
    catalog = {
        1: _item(1),
        2: _item(2, recipe=[{"itemId": 1, "quantity": 1}]),
        3: _item(3, recipe=[{"itemId": 2, "quantity": 1}, {"itemId": 1, "quantity": 1}]),
    }
    events = [
        _event(0, 1000, "ITEM_PURCHASED", participantId=P1, itemId=1),
        _event(1, 1001, "ITEM_PURCHASED", participantId=P1, itemId=1),
        _event(2, 1002, "ITEM_PURCHASED", participantId=P1, itemId=2),
        _event(3, 1003, "ITEM_PURCHASED", participantId=P1, itemId=3),
        _event(4, 1004, "ITEM_PURCHASED", participantId=P1, itemId=9999),
    ]
    catalog[9999] = _item(9999)
    purchases = _reduce(events, catalog_items=catalog)
    assert purchases[2].inventory_before.owned(1) == 2  # antes de comprar B
    assert purchases[3].inventory_before.owned(1) == 1  # B consumiu 1 A
    final = purchases[4].inventory_before
    assert final.owned(1) == 0  # C consumiu o A restante
    assert final.owned(2) == 0  # C consumiu B
    assert final.owned(3) == 1


def test_sale_removes_one_unit() -> None:
    catalog = {1042: _item(1042)}
    events = [
        _event(0, 1000, "ITEM_PURCHASED", participantId=P1, itemId=1042),
        _event(1, 1001, "ITEM_SOLD", participantId=P1, itemId=1042, goldGain=70),
        _event(2, 1002, "ITEM_PURCHASED", participantId=P1, itemId=9999),
    ]
    catalog[9999] = _item(9999)
    purchases = _reduce(events, catalog_items=catalog)
    assert purchases[1].inventory_before.owned(1042) == 0


def test_sale_without_ownership_is_uncertain_never_negative() -> None:
    catalog = {9999: _item(9999)}
    events = [
        _event(0, 1000, "ITEM_SOLD", participantId=P1, itemId=1042, goldGain=70),
        _event(1, 1001, "ITEM_PURCHASED", participantId=P1, itemId=9999),
    ]
    [purchase] = _reduce(events, catalog_items=catalog)
    assert purchase.uncertain is True
    assert any(r.code == "SALE_WITHOUT_OWNERSHIP" for r in purchase.reasons)
    assert purchase.inventory_before.owned(1042) == 0


def test_undo_with_no_prior_operation_is_ambiguous() -> None:
    catalog = {9999: _item(9999)}
    events = [
        _event(0, 1000, "ITEM_UNDO", participantId=P1, beforeId=1042, afterId=0),
        _event(1, 1001, "ITEM_PURCHASED", participantId=P1, itemId=9999),
    ]
    [purchase] = _reduce(events, catalog_items=catalog)
    assert any(r.code == "UNDO_UNCORRELATED" for r in purchase.reasons)


def test_undo_with_mismatched_before_id_is_ambiguous_and_inventory_untouched() -> None:
    catalog = {1042: _item(1042), 9999: _item(9999)}
    events = [
        _event(0, 1000, "ITEM_PURCHASED", participantId=P1, itemId=1042),
        _event(1, 1001, "ITEM_UNDO", participantId=P1, beforeId=7777, afterId=0),
        _event(2, 1002, "ITEM_PURCHASED", participantId=P1, itemId=9999),
    ]
    purchases = _reduce(
        events, catalog_items=catalog
    )  # ITEM_UNDO não produz decisão: purchases = [1042, 9999]
    assert any(r.code == "UNDO_UNCORRELATED" for r in purchases[1].reasons)
    assert purchases[1].inventory_before.owned(1042) == 1  # undo ambíguo nunca reparte pelo palpite


def test_purchase_plus_destroy_reconciled_same_timestamp_no_double_removal() -> None:
    catalog = {
        1042: _item(1042),
        3035: _item(3035, recipe=[{"itemId": 1042, "quantity": 2}]),
    }
    events = [
        _event(0, 1000, "ITEM_PURCHASED", participantId=P1, itemId=1042),
        _event(1, 1001, "ITEM_PURCHASED", participantId=P1, itemId=1042),
        _event(2, 1002, "ITEM_PURCHASED", participantId=P1, itemId=3035),
        # ITEM_DESTROYED real do provedor p/ os 2 componentes consumidos, mesmo timestamp da compra
        _event(3, 1002, "ITEM_DESTROYED", participantId=P1, itemId=1042),
        _event(4, 1002, "ITEM_DESTROYED", participantId=P1, itemId=1042),
        _event(5, 1003, "ITEM_PURCHASED", participantId=P1, itemId=9999),
    ]
    catalog[9999] = _item(9999)
    purchases = _reduce(events, catalog_items=catalog)
    final = purchases[3].inventory_before
    assert (
        final.owned(1042) == 0
    )  # nunca removido duas vezes (uma pela receita, outra pelo destroy)
    assert final.owned(3035) == 1
    assert purchases[3].uncertain is False  # destroys reconciliados não geram incerteza


def test_purchase_plus_destroy_temporal_tie_two_purchases_same_timestamp() -> None:
    """Empate temporal: duas compras de receita no MESMO timestamp, cada uma
    com seu próprio destroy — nunca cruza a reconciliação entre as duas."""
    catalog = {
        1: _item(1),
        2: _item(2),
        10: _item(10, recipe=[{"itemId": 1, "quantity": 1}]),
        20: _item(20, recipe=[{"itemId": 2, "quantity": 1}]),
    }
    events = [
        _event(0, 1000, "ITEM_PURCHASED", participantId=P1, itemId=1),
        _event(1, 1000, "ITEM_PURCHASED", participantId=P1, itemId=2),
        _event(2, 2000, "ITEM_PURCHASED", participantId=P1, itemId=10),  # consome item 1
        _event(3, 2000, "ITEM_PURCHASED", participantId=P1, itemId=20),  # consome item 2
        _event(4, 2000, "ITEM_DESTROYED", participantId=P1, itemId=1),
        _event(5, 2000, "ITEM_DESTROYED", participantId=P1, itemId=2),
        _event(6, 2001, "ITEM_PURCHASED", participantId=P1, itemId=9999),
    ]
    catalog[9999] = _item(9999)
    purchases = _reduce(events, catalog_items=catalog)
    final = purchases[4].inventory_before
    assert final.owned(1) == 0
    assert final.owned(2) == 0
    assert final.owned(10) == 1
    assert final.owned(20) == 1
    assert purchases[4].uncertain is False


def test_unreconciled_destroy_removes_literal_unit_but_flags_uncertain() -> None:
    catalog = {1042: _item(1042), 9999: _item(9999)}
    events = [
        _event(0, 1000, "ITEM_PURCHASED", participantId=P1, itemId=1042),
        # destroy sem compra de receita correspondente neste timestamp
        _event(1, 2000, "ITEM_DESTROYED", participantId=P1, itemId=1042),
        _event(2, 2001, "ITEM_PURCHASED", participantId=P1, itemId=9999),
    ]
    [_, purchase] = _reduce(events, catalog_items=catalog)
    assert purchase.uncertain is True
    assert any(r.code == "UNRECONCILED_ITEM_DESTROYED" for r in purchase.reasons)
    assert purchase.inventory_before.owned(1042) == 0  # fato literal ainda aplicado


def test_destroy_without_ownership_is_uncertain_never_negative() -> None:
    catalog = {9999: _item(9999)}
    events = [
        _event(0, 1000, "ITEM_DESTROYED", participantId=P1, itemId=1042),
        _event(1, 1001, "ITEM_PURCHASED", participantId=P1, itemId=9999),
    ]
    [purchase] = _reduce(events, catalog_items=catalog)
    codes = {r.code for r in purchase.reasons}
    assert "UNRECONCILED_ITEM_DESTROYED" in codes
    assert "DESTROY_WITHOUT_OWNERSHIP" in codes


def test_repeated_identical_events_are_never_deduplicated() -> None:
    """Achado real CC-105: 2047 assinaturas (timestamp,type,itemId,participantId)
    repetidas dentro da mesma partida — cada ocorrência é uma operação
    independente, identidade é o índice (source_index), nunca o conteúdo."""
    catalog = {1042: _item(1042)}
    events = [
        _event(0, 1000, "ITEM_PURCHASED", participantId=P1, itemId=1042),
        _event(1, 1000, "ITEM_PURCHASED", participantId=P1, itemId=1042),
        _event(2, 1001, "ITEM_PURCHASED", participantId=P1, itemId=9999),
    ]
    catalog[9999] = _item(9999)
    purchases = _reduce(events, catalog_items=catalog)
    assert purchases[2].inventory_before.owned(1042) == 2


def test_excluded_item_produces_safe_cause_distinct_from_unknown() -> None:
    events = [_event(0, 1000, "ITEM_PURCHASED", participantId=P1, itemId=555)]
    [purchase] = _reduce(events, catalog_items={}, excluded_item_ids=frozenset({555}))
    assert purchase.uncertain is True
    assert any(r.code == "ITEM_EXCLUDED_UNSUPPORTED" for r in purchase.reasons)


def test_unknown_item_produces_safe_cause_distinct_from_excluded() -> None:
    events = [_event(0, 1000, "ITEM_PURCHASED", participantId=P1, itemId=555)]
    [purchase] = _reduce(events, catalog_items={}, excluded_item_ids=frozenset())
    assert purchase.uncertain is True
    assert any(r.code == "ITEM_UNKNOWN" for r in purchase.reasons)


def test_missing_catalog_produces_safe_cause_for_every_purchase() -> None:
    events = [
        _event(0, 1000, "ITEM_PURCHASED", participantId=P1, itemId=1042),
        _event(1, 1001, "ITEM_PURCHASED", participantId=P1, itemId=9999),
    ]
    purchases = _reduce(events, catalog_id=None, catalog_items=None)
    assert all(p.uncertain for p in purchases)
    assert all(any(r.code == "NO_CATALOG" for r in p.reasons) for p in purchases)
    # melhor-esforço: soma por item_id literal mesmo sem catálogo
    assert purchases[1].inventory_before.owned(1042) == 1


def test_two_participants_interleaved_are_isolated() -> None:
    catalog = {1042: _item(1042), 2003: _item(2003)}
    events = [
        _event(0, 1000, "ITEM_PURCHASED", participantId=P1, itemId=1042),
        _event(1, 1001, "ITEM_PURCHASED", participantId=P6, itemId=2003),
        _event(2, 1002, "ITEM_PURCHASED", participantId=P1, itemId=2003),
    ]
    p1_purchases = _reduce(events, participant_id=P1, catalog_items=catalog)
    assert len(p1_purchases) == 2
    assert p1_purchases[1].inventory_before.owned(2003) == 0  # nunca herda a compra do P6

    p6_purchases = _reduce(events, participant_id=P6, catalog_items=catalog)
    assert len(p6_purchases) == 1
    assert p6_purchases[0].purchased_item_id == 2003


def test_malformed_payload_for_own_participant_raises() -> None:
    events = [_event(0, 1000, "ITEM_PURCHASED", participantId=P1, itemId="not-an-int")]
    with pytest.raises(TimelineReplayError) as excinfo:
        _reduce(events, catalog_items={})
    assert excinfo.value.code == "TIMELINE_EVENT_MALFORMED"


def test_malformed_payload_for_another_participant_never_raises() -> None:
    events = [
        _event(0, 1000, "ITEM_PURCHASED", participantId=P6, itemId="not-an-int"),
        _event(1, 1001, "ITEM_PURCHASED", participantId=P1, itemId=1042),
    ]
    purchases = _reduce(events, participant_id=P1, catalog_items={1042: _item(1042)})
    assert len(purchases) == 1  # evento corrompido de outro participante nunca contamina este


def test_perturbing_events_after_cutoff_preserves_earlier_snapshot() -> None:
    catalog = {1042: _item(1042), 2003: _item(2003), 9999: _item(9999)}
    base = [
        _event(0, 1000, "ITEM_PURCHASED", participantId=P1, itemId=1042),
        _event(1, 2000, "ITEM_PURCHASED", participantId=P1, itemId=2003),
    ]
    run_a = base + [_event(2, 3000, "ITEM_PURCHASED", participantId=P1, itemId=9999)]
    run_b = base + [
        _event(2, 2500, "ITEM_SOLD", participantId=P1, itemId=1042, goldGain=10),
        _event(3, 3000, "ITEM_PURCHASED", participantId=P1, itemId=9999),
    ]
    purchases_a = _reduce(run_a, catalog_items=catalog)
    purchases_b = _reduce(run_b, catalog_items=catalog)
    # snapshot ANTES da 2a compra (source_index=1) é idêntico nos dois casos,
    # apesar de run_b divergir depois desse ponto (venda inserida em seguida)
    assert purchases_a[1].inventory_before == purchases_b[1].inventory_before


def test_snapshots_are_independent_frozen_values() -> None:
    catalog = {1042: _item(1042), 2003: _item(2003)}
    events = [
        _event(0, 1000, "ITEM_PURCHASED", participantId=P1, itemId=1042),
        _event(1, 2000, "ITEM_PURCHASED", participantId=P1, itemId=2003),
    ]
    purchases = _reduce(events, catalog_items=catalog)
    first_payload = purchases[0].inventory_before.to_payload()
    second_payload = purchases[1].inventory_before.to_payload()
    first_payload["items"]["9999"] = 99  # mutar a cópia do primeiro nunca afeta o segundo
    assert "9999" not in second_payload["items"]
    assert purchases[1].inventory_before.owned(1042) == 1  # estado real preservado


def test_trinket_purchase_never_occupies_the_six_slots() -> None:
    catalog = {
        3340: _item(3340, tags=("Active", "Trinket", "Vision")),
        3364: _item(3364, tags=("Active", "Trinket", "Vision")),
        9999: _item(9999),
    }
    events = [
        _event(0, 1000, "ITEM_PURCHASED", participantId=P1, itemId=3340),
        _event(1, 2000, "ITEM_PURCHASED", participantId=P1, itemId=3364),
        _event(2, 3000, "ITEM_PURCHASED", participantId=P1, itemId=9999),
    ]
    purchases = _reduce(events, catalog_items=catalog)
    assert purchases[0].inventory_before.trinket_item_id is None
    assert purchases[1].inventory_before.trinket_item_id == 3340
    assert purchases[2].inventory_before.trinket_item_id == 3364
    # nunca aparece no multiconjunto normal, nem o antigo nem o novo
    assert purchases[2].inventory_before.owned(3340) == 0
    assert purchases[2].inventory_before.owned(3364) == 0
