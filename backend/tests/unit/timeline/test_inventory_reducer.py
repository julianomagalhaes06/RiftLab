"""Reducer puro de reconstrução de inventário (CC-203):
reconstruct_participant_purchases. Sem I/O, sem Postgres, sem rede — testa
comportamento e falha do reducer em isolamento, não a persistência
(ver tests/integration/test_timeline_reconstruction.py para o orquestrador).

Os dados brutos reais não são versionados: as sequências de
compra+destroy/empate temporal abaixo são sintéticas, construídas a partir
do schema confirmado por uma auditoria de dados reais (beforeId/afterId
presentes em 100% dos ITEM_UNDO reais; eventos repetidos com mesma assinatura
ocorrem de verdade) e da forma dos eventos observada num corpus real pequeno
(10 partidas, patches 14.20 e 15.1). Não são partidas reais nem provam
semântica oficial da Riot."""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from riftlab.catalog.domain import Item
from riftlab.shared.domain import SourceOrigin
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
    item_id: int,
    recipe: list[dict[str, Any]] | None = None,
    tags: tuple[str, ...] = (),
    flags: dict[str, Any] | None = None,
) -> Item:
    return Item(
        catalog_id=CATALOG_ID,
        item_id=item_id,
        price=100,
        recipe=recipe or [],
        attributes={"tags": list(tags)},
        flags=flags or {},
    )


def _reduce(
    events: list[TimelineEvent],
    *,
    participant_id: int = P1,
    origin: SourceOrigin = SourceOrigin.CURATED_DEMO,
    catalog_id: uuid.UUID | None = CATALOG_ID,
    catalog_items: dict[int, Item] | None = None,
    excluded_item_ids: frozenset[int] = frozenset(),
):
    return reconstruct_participant_purchases(
        participant_id=participant_id,
        events=events,
        origin=origin,
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


# ---------------------------------------------------------------------------
# Regressões de falhas encontradas em revisão do reducer — cada uma
# reproduzida e testada isoladamente.
# ---------------------------------------------------------------------------


def test_undo_with_afterid_not_matching_any_consumed_component_is_contradictory() -> None:
    """Achado 1: comparar só beforeId não comprova correlação — item 1 não
    tem receita (nada consumido), então afterId só pode ser 0; afterId=999
    é uma contradição, nunca aplicada às cegas mesmo com beforeId correto."""
    catalog = {1: _item(1), 4: _item(4)}
    events = [
        _event(0, 1000, "ITEM_PURCHASED", participantId=P1, itemId=1),
        _event(1, 2000, "ITEM_UNDO", participantId=P1, beforeId=1, afterId=999),
        _event(2, 3000, "ITEM_PURCHASED", participantId=P1, itemId=4),
    ]
    purchases = _reduce(events, catalog_items=catalog)
    assert purchases[1].inventory_before.owned(1) == 1  # nunca reparte pelo palpite
    assert purchases[1].uncertain is True
    assert any(r.code == "UNDO_UNCORRELATED" for r in purchases[1].reasons)


def test_undo_with_afterid_matching_consumed_component_is_accepted() -> None:
    """afterId=0 é sempre válido (nenhum item visível na própria slot da
    compra desfeita); testa explicitamente que um afterId=0 continua
    aceito quando a compra desfeita consumiu componentes de verdade."""
    catalog = {1: _item(1), 2: _item(2, recipe=[{"itemId": 1, "quantity": 1}])}
    events = [
        _event(0, 1000, "ITEM_PURCHASED", participantId=P1, itemId=1),
        _event(1, 2000, "ITEM_PURCHASED", participantId=P1, itemId=2),
        _event(2, 3000, "ITEM_UNDO", participantId=P1, beforeId=2, afterId=0),
    ]
    purchases = _reduce(events, catalog_items=catalog)
    assert purchases[-1].uncertain is False


def test_undo_of_sale_restores_sold_item() -> None:
    """Undo de venda restaura o item vendido (venda é uma operação
    reversível, como a compra).

    Forma do evento: um undo de VENDA observado em 16/16 casos no corpus
    real analisado (10 partidas, patches 14.20 e 15.1) usa beforeId=0/
    afterId=<item vendido> — a forma OPOSTA de um undo de COMPRA
    (beforeId=<item>/afterId=0). goldGain nesses 16 casos é
    majoritariamente negativo (14/16), mas 2/16 têm goldGain=0 — goldGain
    não é parte da forma estrutural correlacionada (beforeId/afterId),
    nunca usado como condição de correlação. Ver
    test_undo_of_sale_purchase_shaped_event_never_correlates logo abaixo
    para o contraste explícito."""
    catalog = {1: _item(1), 9: _item(9)}
    events = [
        _event(0, 1000, "ITEM_PURCHASED", participantId=P1, itemId=1),
        _event(1, 2000, "ITEM_SOLD", participantId=P1, itemId=1, goldGain=50),
        _event(2, 3000, "ITEM_UNDO", participantId=P1, beforeId=0, afterId=1, goldGain=-50),
        _event(3, 4000, "ITEM_PURCHASED", participantId=P1, itemId=9),
    ]
    purchases = _reduce(events, catalog_items=catalog)
    assert purchases[-1].inventory_before.owned(1) == 1  # venda desfeita, item restaurado
    assert purchases[-1].uncertain is False


def test_undo_of_sale_purchase_shaped_event_never_correlates() -> None:
    """Contraste explícito com o teste acima: o MESMO cenário, mas o evento
    ITEM_UNDO usa a forma de undo de COMPRA (beforeId=<item>, afterId=0) em
    vez da forma de venda observada no dado real -- nunca deve correlacionar
    contra uma venda, mesmo com o item certo em beforeId."""
    catalog = {1: _item(1), 9: _item(9)}
    events = [
        _event(0, 1000, "ITEM_PURCHASED", participantId=P1, itemId=1),
        _event(1, 2000, "ITEM_SOLD", participantId=P1, itemId=1, goldGain=50),
        _event(2, 3000, "ITEM_UNDO", participantId=P1, beforeId=1, afterId=0),
        _event(3, 4000, "ITEM_PURCHASED", participantId=P1, itemId=9),
    ]
    purchases = _reduce(events, catalog_items=catalog)
    assert purchases[-1].inventory_before.owned(1) == 0  # venda NÃO desfeita
    assert any(r.code == "UNDO_UNCORRELATED" for r in purchases[-1].reasons)


def test_undo_of_sale_gates_eligibility_for_real_origin() -> None:
    """Mesmo princípio de test_undo_correlation_gates_eligibility_for_real_
    origin, agora para a forma de venda: correlacionar (pela regra interna)
    em dado real também marca incerto, nunca alegado como comprovado."""
    catalog = {1: _item(1), 9: _item(9)}
    events = [
        _event(0, 1000, "ITEM_PURCHASED", participantId=P1, itemId=1),
        _event(1, 2000, "ITEM_SOLD", participantId=P1, itemId=1, goldGain=50),
        _event(2, 3000, "ITEM_UNDO", participantId=P1, beforeId=0, afterId=1, goldGain=-50),
        _event(3, 4000, "ITEM_PURCHASED", participantId=P1, itemId=9),
    ]
    purchases = _reduce(events, origin=SourceOrigin.COMPLETED_MATCH, catalog_items=catalog)
    final = purchases[-1]
    assert final.inventory_before.owned(1) == 1  # restauração continua correta
    assert final.uncertain is True
    assert any(r.code == "UNVALIDATED_UNDO_SEMANTICS" for r in final.reasons)


def test_undo_of_sale_intervening_purchase_invalidates_ledger() -> None:
    """ "Operações intervenientes" (mesmo princípio de
    test_undo_intervening_unrelated_purchase_invalidates_earlier_ledger),
    agora para venda: vender X, comprar Y, tentar desfazer a venda de X
    (beforeId=0, afterId=X) -- a operação reversível mais recente é a
    compra de Y, não a venda de X, então correlacionar é ambíguo."""
    catalog = {1: _item(1), 2: _item(2), 9: _item(9)}
    events = [
        _event(0, 1000, "ITEM_PURCHASED", participantId=P1, itemId=1),
        _event(1, 2000, "ITEM_SOLD", participantId=P1, itemId=1, goldGain=50),
        _event(2, 3000, "ITEM_PURCHASED", participantId=P1, itemId=2),
        _event(3, 4000, "ITEM_UNDO", participantId=P1, beforeId=0, afterId=1, goldGain=-50),
        _event(4, 5000, "ITEM_PURCHASED", participantId=P1, itemId=9),
    ]
    purchases = _reduce(events, catalog_items=catalog)
    assert purchases[-1].inventory_before.owned(1) == 0  # venda de 1 NÃO desfeita
    assert any(r.code == "UNDO_UNCORRELATED" for r in purchases[-1].reasons)


def test_undo_of_sale_contradictory_after_id_never_applied() -> None:
    """beforeId=0 batendo com a forma de venda não basta -- afterId precisa
    ser exatamente o item vendido, nunca aplicado às cegas com outro item."""
    catalog = {1: _item(1), 2: _item(2), 9: _item(9)}
    events = [
        _event(0, 1000, "ITEM_PURCHASED", participantId=P1, itemId=1),
        _event(1, 2000, "ITEM_SOLD", participantId=P1, itemId=1, goldGain=50),
        _event(2, 3000, "ITEM_UNDO", participantId=P1, beforeId=0, afterId=2, goldGain=-50),
        _event(3, 4000, "ITEM_PURCHASED", participantId=P1, itemId=9),
    ]
    purchases = _reduce(events, catalog_items=catalog)
    assert purchases[-1].inventory_before.owned(1) == 0
    assert any(r.code == "UNDO_UNCORRELATED" for r in purchases[-1].reasons)


def test_undo_intervening_unrelated_purchase_invalidates_earlier_ledger() -> None:
    """ "Operações intervenientes": comprar A, comprar B, tentar desfazer A
    (beforeId=A) — a operação reversível mais recente é a compra de B, não a
    de A, então correlacionar com A é ambíguo, nunca aplicado."""
    catalog = {1: _item(1), 2: _item(2)}
    events = [
        _event(0, 1000, "ITEM_PURCHASED", participantId=P1, itemId=1),
        _event(1, 2000, "ITEM_PURCHASED", participantId=P1, itemId=2),
        _event(2, 3000, "ITEM_UNDO", participantId=P1, beforeId=1, afterId=0),
        _event(3, 4000, "ITEM_PURCHASED", participantId=P1, itemId=1),
    ]
    purchases = _reduce(events, catalog_items=catalog)
    assert any(r.code == "UNDO_UNCORRELATED" for r in purchases[-1].reasons)
    assert purchases[-1].inventory_before.owned(1) == 1  # não removido pelo undo ambíguo
    assert purchases[-1].inventory_before.owned(2) == 1  # item de B nunca é tocado


def test_multiple_identical_purchases_only_the_most_recent_is_undoable() -> None:
    """ "Várias compras iguais": duas compras do mesmo item base; undo desfaz
    só a mais recente (ledger por operação, nunca por tipo de item)."""
    catalog = {1: _item(1), 9: _item(9)}
    events = [
        _event(0, 1000, "ITEM_PURCHASED", participantId=P1, itemId=1),
        _event(1, 2000, "ITEM_PURCHASED", participantId=P1, itemId=1),
        _event(2, 3000, "ITEM_UNDO", participantId=P1, beforeId=1, afterId=0),
        _event(3, 4000, "ITEM_PURCHASED", participantId=P1, itemId=9),
    ]
    purchases = _reduce(events, catalog_items=catalog)
    assert purchases[-1].inventory_before.owned(1) == 1  # só a segunda unidade foi desfeita
    assert purchases[-1].uncertain is False


def test_undo_of_recipe_purchase_clears_pending_destroys_for_that_purchase() -> None:
    """ "Ledger/pending_destroys após undo": compra de receita no MESMO
    timestamp de um undo subsequente; um ITEM_DESTROYED chegando depois, no
    mesmo timestamp, para o componente da compra JÁ DESFEITA nunca deve se
    reconciliar contra ela — fica não reconciliado (a compra nunca
    "aconteceu" do ponto de vista pós-undo)."""
    catalog = {1: _item(1), 2: _item(2, recipe=[{"itemId": 1, "quantity": 1}]), 9: _item(9)}
    events = [
        _event(0, 5000, "ITEM_PURCHASED", participantId=P1, itemId=1),
        _event(1, 5000, "ITEM_PURCHASED", participantId=P1, itemId=2),  # consome o componente 1
        _event(2, 5000, "ITEM_UNDO", participantId=P1, beforeId=2, afterId=0),  # desfaz a compra
        _event(3, 5000, "ITEM_DESTROYED", participantId=P1, itemId=1),  # chega depois, mesmo ts
        _event(4, 6000, "ITEM_PURCHASED", participantId=P1, itemId=9),
    ]
    purchases = _reduce(events, catalog_items=catalog)
    final = purchases[-1]
    # destroy não reconciliado (a compra que "justificaria" foi desfeita) —
    # remove o item 1 de novo (agora incerto), nunca silenciosamente ignorado
    assert any(r.code == "UNRECONCILED_ITEM_DESTROYED" for r in final.reasons)
    assert final.inventory_before.owned(1) == 0


def test_recipe_consumes_transitive_subcomponent_when_intermediate_not_owned() -> None:
    """Achado 2: A->B->C, possui A, compra C diretamente (nunca comprou B) —
    a receita de C precisa decompor B em A quando B não está na posse."""
    catalog = {
        1: _item(1),
        2: _item(2, recipe=[{"itemId": 1, "quantity": 1}]),
        3: _item(3, recipe=[{"itemId": 2, "quantity": 1}]),
    }
    events = [
        _event(0, 1000, "ITEM_PURCHASED", participantId=P1, itemId=1),
        _event(1, 2000, "ITEM_PURCHASED", participantId=P1, itemId=3),  # pula B
        _event(2, 3000, "ITEM_PURCHASED", participantId=P1, itemId=9999),
    ]
    catalog[9999] = _item(9999)
    purchases = _reduce(events, catalog_items=catalog)
    final = purchases[-1]
    assert final.inventory_before.owned(1) == 0  # decomposto via B, nunca sobra
    assert final.inventory_before.owned(2) == 0  # B nunca chega a existir (virtual)
    assert final.inventory_before.owned(3) == 1
    assert final.uncertain is False


def test_recipe_prefers_owned_complete_component_over_decomposing_it() -> None:
    """ "Preferência explícita pelo componente já completo": possui B pronto
    (comprado) e A avulso; comprar C (recipe=[B]) deve consumir o B já
    pronto, nunca decompor A à toa nem sobrar B intocado."""
    catalog = {
        1: _item(1),
        2: _item(2, recipe=[{"itemId": 1, "quantity": 1}]),
        3: _item(3, recipe=[{"itemId": 2, "quantity": 1}]),
    }
    events = [
        _event(0, 1000, "ITEM_PURCHASED", participantId=P1, itemId=1),  # A avulso
        _event(1, 1000, "ITEM_PURCHASED", participantId=P1, itemId=1),  # mais um A avulso
        _event(2, 2000, "ITEM_PURCHASED", participantId=P1, itemId=2),  # B pronto (consome 1 A)
        _event(3, 3000, "ITEM_PURCHASED", participantId=P1, itemId=3),  # C consome o B pronto
        _event(4, 4000, "ITEM_PURCHASED", participantId=P1, itemId=9999),
    ]
    catalog[9999] = _item(9999)
    purchases = _reduce(events, catalog_items=catalog)
    final = purchases[-1]
    assert final.inventory_before.owned(1) == 1  # o A avulso sobrando nunca é tocado
    assert final.inventory_before.owned(2) == 0  # B pronto foi consumido, nunca duplicado
    assert final.inventory_before.owned(3) == 1


def test_recipe_repeated_subcomponent_multiplicity_via_transitive_path() -> None:
    """Componentes repetidos por um caminho transitivo: C precisa de 2 B, só
    A é possuído (2 unidades) — decompõe as duas ocorrências de B sem
    sobrar/faltar nenhuma unidade de A."""
    catalog = {
        1: _item(1),
        2: _item(2, recipe=[{"itemId": 1, "quantity": 1}]),
        3: _item(3, recipe=[{"itemId": 2, "quantity": 2}]),
    }
    events = [
        _event(0, 1000, "ITEM_PURCHASED", participantId=P1, itemId=1),
        _event(1, 1000, "ITEM_PURCHASED", participantId=P1, itemId=1),
        _event(2, 2000, "ITEM_PURCHASED", participantId=P1, itemId=3),
        _event(3, 3000, "ITEM_PURCHASED", participantId=P1, itemId=9999),
    ]
    catalog[9999] = _item(9999)
    purchases = _reduce(events, catalog_items=catalog)
    final = purchases[-1]
    assert final.inventory_before.owned(1) == 0
    assert final.inventory_before.owned(3) == 1


def test_inventory_capacity_overflow_marks_uncertain() -> None:
    """Achado 2: 7 itens normais distintos (sem regra de stack) excede as 6
    slots normais — nunca passa despercebido."""
    catalog = {i: _item(i) for i in range(1, 8)}
    events = [
        _event(i - 1, i * 1000, "ITEM_PURCHASED", participantId=P1, itemId=i) for i in range(1, 8)
    ]
    purchases = _reduce(events, catalog_items=catalog)
    assert purchases[-1].uncertain is True
    assert any(r.code == "INVENTORY_CAPACITY_EXCEEDED" for r in purchases[-1].reasons)


def test_inventory_within_six_slots_never_flags_capacity() -> None:
    catalog = {i: _item(i) for i in range(1, 7)}
    events = [
        _event(i - 1, i * 1000, "ITEM_PURCHASED", participantId=P1, itemId=i) for i in range(1, 7)
    ]
    purchases = _reduce(events, catalog_items=catalog)
    assert purchases[-1].uncertain is False


def test_stackable_consumable_within_declared_limit_never_overflows() -> None:
    """Stack suportado: 5 poções (stacks=5, declarado pelo catálogo) cabem
    numa slot só, mesmo com outros 5 itens normais preenchendo o resto."""
    catalog = {i: _item(i) for i in range(1, 6)}
    catalog[100] = _item(100, flags={"consumed": True, "stacks": 5})
    events = [
        _event(i - 1, i * 1000, "ITEM_PURCHASED", participantId=P1, itemId=i) for i in range(1, 6)
    ]
    events += [
        _event(4 + n, (5 + n) * 1000, "ITEM_PURCHASED", participantId=P1, itemId=100)
        for n in range(5)
    ]
    purchases = _reduce(events, catalog_items=catalog)
    assert purchases[-1].uncertain is False
    assert purchases[-1].inventory_before.owned(100) == 4  # antes da 5a poção


def test_stack_rule_unknown_for_consumable_without_declared_stacks() -> None:
    """Stack desconhecido: item consumível sem `stacks` declarado nunca tem
    capacidade inventada — fica explicitamente incerto."""
    catalog = {1: _item(1, flags={"consumed": True})}
    events = [_event(0, 1000, "ITEM_PURCHASED", participantId=P1, itemId=1)]
    purchases = _reduce(events, catalog_items=catalog)
    assert purchases[-1].uncertain is True
    assert any(r.code == "ITEM_STACK_RULE_UNKNOWN" for r in purchases[-1].reasons)


def test_destroy_reconciliation_heuristic_flag_propagates_forward_only_demo() -> None:
    """Reconciliação de destroy é sempre uma estimativa, propagada
    explicitamente em toda decisão a partir do ponto em que ocorre — nunca
    contamina uma decisão anterior. EM DEMO (origin default deste helper),
    a regra sintética é verificada por este próprio teste, então pode
    ficar uncertain=False; em dado real NUNCA (ver
    test_destroy_reconciliation_heuristic_gates_eligibility_for_real_origin)."""
    catalog = {1: _item(1), 2: _item(2, recipe=[{"itemId": 1, "quantity": 1}]), 9: _item(9)}
    events = [
        _event(
            0, 1000, "ITEM_PURCHASED", participantId=P1, itemId=1
        ),  # antes: nunca usa heurística
        _event(1, 2000, "ITEM_PURCHASED", participantId=P1, itemId=2),  # consome componente 1
        _event(2, 2000, "ITEM_DESTROYED", participantId=P1, itemId=1),  # reconciliado
        _event(3, 3000, "ITEM_PURCHASED", participantId=P1, itemId=9),  # depois: heurística usada
    ]
    purchases = _reduce(events, catalog_items=catalog)
    assert purchases[0].destroy_reconciliation_heuristic_used is False
    assert purchases[1].destroy_reconciliation_heuristic_used is False  # ainda antes do destroy
    assert purchases[2].destroy_reconciliation_heuristic_used is True
    # em DEMO, reconciliação não bloqueia sozinha (regra sintética verificada aqui mesmo)
    assert purchases[2].uncertain is False


def test_destroy_reconciliation_heuristic_gates_eligibility_for_real_origin() -> None:
    """Um sinalizador só de documentação não basta: compra A, compra B
    consome A, destroy A no mesmo timestamp, compra C, com
    origin=COMPLETED_MATCH — bater com a heurística de reconciliação NUNCA
    é certeza fora de DEMO. Dupla contagem continua prevenida
    (owned(1)==0), mas o participante fica marcado incerto a partir da
    reconciliação."""
    catalog = {1: _item(1), 2: _item(2, recipe=[{"itemId": 1, "quantity": 1}]), 9: _item(9)}
    events = [
        _event(0, 1000, "ITEM_PURCHASED", participantId=P1, itemId=1),
        _event(1, 2000, "ITEM_PURCHASED", participantId=P1, itemId=2),
        _event(2, 2000, "ITEM_DESTROYED", participantId=P1, itemId=1),
        _event(3, 3000, "ITEM_PURCHASED", participantId=P1, itemId=9),
    ]
    purchases = _reduce(events, origin=SourceOrigin.COMPLETED_MATCH, catalog_items=catalog)
    assert purchases[1].uncertain is False  # antes da reconciliação, ainda certo
    assert purchases[2].destroy_reconciliation_heuristic_used is True
    assert purchases[2].uncertain is True  # gate operacional, não só documental
    assert any(r.code == "UNVALIDATED_DESTROY_RECONCILIATION" for r in purchases[2].reasons)
    assert purchases[2].inventory_before.owned(1) == 0  # dupla contagem continua prevenida


def test_undo_correlation_gates_eligibility_for_real_origin() -> None:
    """Validar afterId por conjunto {0, componentes} não comprova semântica
    real por si só — um undo CORRELACIONADO (pela regra interna) em dado
    real também marca incerto, nunca alegado como comprovado fora da
    evidência."""
    catalog = {1042: _item(1042), 3035: _item(3035, recipe=[{"itemId": 1042, "quantity": 1}])}
    events = [
        _event(0, 1000, "ITEM_PURCHASED", participantId=P1, itemId=1042),
        _event(1, 1001, "ITEM_PURCHASED", participantId=P1, itemId=3035),
        _event(2, 1002, "ITEM_UNDO", participantId=P1, beforeId=3035, afterId=0),
        _event(3, 1003, "ITEM_PURCHASED", participantId=P1, itemId=9999),
    ]
    catalog[9999] = _item(9999)
    purchases = _reduce(events, origin=SourceOrigin.COMPLETED_MATCH, catalog_items=catalog)
    final = purchases[-1]
    assert final.inventory_before.owned(1042) == 1  # restauração continua correta
    assert final.inventory_before.owned(3035) == 0
    assert final.uncertain is True  # correlacionado pela regra interna, mas nunca comprovado
    assert any(r.code == "UNVALIDATED_UNDO_SEMANTICS" for r in final.reasons)


def test_undo_correlation_stays_certain_for_demo_origin() -> None:
    """Contraste direto com o teste acima: mesmo cenário, origin=CURATED_DEMO
    (default deste helper) — a regra sintética é verificada por
    test_undo_restores_proven_state_including_consumed_components, então
    pode ficar uncertain=False."""
    catalog = {1042: _item(1042), 3035: _item(3035, recipe=[{"itemId": 1042, "quantity": 1}])}
    events = [
        _event(0, 1000, "ITEM_PURCHASED", participantId=P1, itemId=1042),
        _event(1, 1001, "ITEM_PURCHASED", participantId=P1, itemId=3035),
        _event(2, 1002, "ITEM_UNDO", participantId=P1, beforeId=3035, afterId=0),
        _event(3, 1003, "ITEM_PURCHASED", participantId=P1, itemId=9999),
    ]
    catalog[9999] = _item(9999)
    purchases = _reduce(events, catalog_items=catalog)
    assert purchases[-1].uncertain is False


def test_unknown_items_also_count_toward_six_slot_capacity() -> None:
    """Capacidade é verificada em TODO caminho de compra, não só quando o
    item é conhecido do catálogo — um item desconhecido ainda ocupa uma
    slot normal de verdade."""
    catalog: dict[int, Item] = {}  # nenhum item conhecido: tudo vira ITEM_UNKNOWN
    events = [
        _event(i - 1, i * 1000, "ITEM_PURCHASED", participantId=P1, itemId=i) for i in range(1, 8)
    ]
    purchases = _reduce(events, catalog_items=catalog)
    assert any(r.code == "INVENTORY_CAPACITY_EXCEEDED" for r in purchases[-1].reasons)


# ---------------------------------------------------------------------------
# Destroy dos componentes ANTES da compra composta, no mesmo timestamp (no
# corpus real analisado, 851/851 destroys com compra co-temporal precedem a
# compra).
# ---------------------------------------------------------------------------


def test_destroy_before_purchase_reconciles_repeated_components() -> None:
    """Compra 2x A, destroy A, destroy A, compra B (precisa 2xA) no mesmo
    timestamp -- nunca remove a mesma unidade duas vezes, reconhece a
    operação composta observada."""
    catalog = {1042: _item(1042), 3035: _item(3035, recipe=[{"itemId": 1042, "quantity": 2}])}
    events = [
        _event(0, 1000, "ITEM_PURCHASED", participantId=P1, itemId=1042),
        _event(1, 1001, "ITEM_PURCHASED", participantId=P1, itemId=1042),
        _event(2, 2000, "ITEM_DESTROYED", participantId=P1, itemId=1042),
        _event(3, 2000, "ITEM_DESTROYED", participantId=P1, itemId=1042),
        _event(4, 2000, "ITEM_PURCHASED", participantId=P1, itemId=3035),
        _event(5, 3000, "ITEM_PURCHASED", participantId=P1, itemId=9999),
    ]
    catalog[9999] = _item(9999)
    purchases = _reduce(events, catalog_items=catalog, origin=SourceOrigin.COMPLETED_MATCH)
    compound = purchases[2]  # snapshot da compra de 3035 (idx4)
    assert compound.purchased_item_id == 3035
    # v6 (transação): os 2 destroys reivindicados por esta compra fazem parte da
    # transação dela; o snapshot fica ANTES deles. Na v5 este teste afirmava 0
    # ("já removido pelos destroys"), o que omitia exatamente a receita do alvo.
    assert compound.inventory_before.owned(1042) == 2
    assert not compound.pre_decision_uncertain  # sem as marcas dos destroys reivindicados
    assert not any(r.code == "UNRECONCILED_ITEM_DESTROYED" for r in compound.reasons)
    assert compound.destroy_order_reconciliation_used is True
    assert any(r.code == "UNVALIDATED_DESTROY_ORDER_RECONCILIATION" for r in compound.reasons)
    # Identidade por EVENTO, não só contagem por compra --
    # UNVALIDATED_DESTROY_ORDER_RECONCILIATION é emitido UMA vez
    # por COMPRA (não uma vez por unidade), então contar reason codes NUNCA
    # mede quantos destroys/unidades foram alocados; destroy_order_claimed_
    # source_indexes é o dado correto para essa contagem (2 unidades aqui,
    # 1 reason code só).
    assert set(compound.destroy_order_claimed_source_indexes) == {2, 3}
    assert (
        sum(1 for r in compound.reasons if r.code == "UNVALIDATED_DESTROY_ORDER_RECONCILIATION")
        == 1
    )
    # nunca removida duas vezes: a compra seguinte (idx5) só vê 3035 no
    # inventário, nunca 1042 residual nem contagem negativa/duplicada.
    final = purchases[-1]
    assert final.inventory_before.owned(1042) == 0
    assert final.inventory_before.owned(3035) == 1


def test_destroy_before_purchase_never_removes_surviving_unit() -> None:
    """Possuir 2xA, destruir SÓ 1, comprar B (precisa 1xA) -- a unidade
    SOBREVIVENTE (nunca destruída) deve permanecer intocada; só a unidade
    JÁ destruída conta como consumo de B. Resultado incorreto seria zero A
    restantes (a unidade sobrevivente consumida também: dupla remoção)."""
    catalog = {1042: _item(1042), 3035: _item(3035, recipe=[{"itemId": 1042, "quantity": 1}])}
    catalog[9999] = _item(9999)
    events = [
        _event(0, 1000, "ITEM_PURCHASED", participantId=P1, itemId=1042),
        _event(1, 1001, "ITEM_PURCHASED", participantId=P1, itemId=1042),
        _event(2, 2000, "ITEM_DESTROYED", participantId=P1, itemId=1042),
        _event(3, 2000, "ITEM_PURCHASED", participantId=P1, itemId=3035),
        _event(4, 3000, "ITEM_PURCHASED", participantId=P1, itemId=9999),
    ]
    purchases = _reduce(events, catalog_items=catalog, origin=SourceOrigin.COMPLETED_MATCH)
    compound = purchases[2]
    assert compound.destroy_order_claimed_source_indexes == (2,)
    final = purchases[-1]
    assert final.inventory_before.owned(1042) == 1  # unidade sobrevivente, intocada
    assert final.inventory_before.owned(3035) == 1


def test_destroy_before_purchase_excess_ownership_never_consumed() -> None:
    """Mesmo princípio com posse ainda maior: 3xA possuídas, só 1 destruída,
    B precisa de 1xA -- as outras 2 unidades sobreviventes permanecem
    intocadas, tanto ANTES quanto DEPOIS de B ser aplicada."""
    catalog = {1042: _item(1042), 3035: _item(3035, recipe=[{"itemId": 1042, "quantity": 1}])}
    catalog[9999] = _item(9999)
    events = [
        _event(0, 1000, "ITEM_PURCHASED", participantId=P1, itemId=1042),
        _event(1, 1001, "ITEM_PURCHASED", participantId=P1, itemId=1042),
        _event(2, 1002, "ITEM_PURCHASED", participantId=P1, itemId=1042),
        _event(3, 2000, "ITEM_DESTROYED", participantId=P1, itemId=1042),
        _event(4, 2000, "ITEM_PURCHASED", participantId=P1, itemId=3035),
        _event(5, 3000, "ITEM_PURCHASED", participantId=P1, itemId=9999),
    ]
    purchases = _reduce(events, catalog_items=catalog, origin=SourceOrigin.COMPLETED_MATCH)
    compound = purchases[3]  # snapshot da própria compra de 3035 (ANTES da transação)
    # v6: as 3 unidades (a destruída reivindicada volta ao snapshot; na v5 eram 2)
    assert compound.inventory_before.owned(1042) == 3
    final = purchases[-1]  # snapshot de 9999 (DEPOIS de 3035 aplicada)
    assert final.inventory_before.owned(1042) == 2
    assert final.inventory_before.owned(3035) == 1


def test_destroy_before_purchase_partial_quantity_never_overclaims() -> None:
    """Só 1 unidade destruída, receita precisa de 2 -- reivindica só o que
    existe de verdade, nunca inventa a segunda unidade."""
    catalog = {1042: _item(1042), 3035: _item(3035, recipe=[{"itemId": 1042, "quantity": 2}])}
    events = [
        _event(0, 1000, "ITEM_PURCHASED", participantId=P1, itemId=1042),
        _event(1, 2000, "ITEM_DESTROYED", participantId=P1, itemId=1042),
        _event(2, 2000, "ITEM_PURCHASED", participantId=P1, itemId=3035),
        _event(3, 3000, "ITEM_PURCHASED", participantId=P1, itemId=9999),
    ]
    catalog[9999] = _item(9999)
    purchases = _reduce(events, catalog_items=catalog, origin=SourceOrigin.COMPLETED_MATCH)
    compound = purchases[1]
    assert compound.destroy_order_reconciliation_used is True  # 1 unidade reivindicada
    assert any(r.code == "UNVALIDATED_DESTROY_ORDER_RECONCILIATION" for r in compound.reasons)
    assert compound.destroy_order_claimed_source_indexes == (1,)  # só 1 unidade, nunca 2


def test_destroy_before_purchase_ancestral_component() -> None:
    """Destroy do componente de PRIMEIRO nível (A), compra de um item que
    precisa de um item INTERMEDIÁRIO (C, nunca comprado) cuja receita
    precisa de A -- a reivindicação atravessa a recursão de _consume_recipe
    normalmente, sem tratamento especial."""
    catalog = {
        1042: _item(1042),
        5001: _item(5001, recipe=[{"itemId": 1042, "quantity": 2}]),  # C
        5002: _item(5002, recipe=[{"itemId": 5001, "quantity": 1}]),  # B, precisa de C
    }
    events = [
        _event(0, 1000, "ITEM_PURCHASED", participantId=P1, itemId=1042),
        _event(1, 1001, "ITEM_PURCHASED", participantId=P1, itemId=1042),
        _event(2, 2000, "ITEM_DESTROYED", participantId=P1, itemId=1042),
        _event(3, 2000, "ITEM_DESTROYED", participantId=P1, itemId=1042),
        _event(4, 2000, "ITEM_PURCHASED", participantId=P1, itemId=5002),
        _event(5, 3000, "ITEM_PURCHASED", participantId=P1, itemId=9999),
    ]
    catalog[9999] = _item(9999)
    purchases = _reduce(events, catalog_items=catalog, origin=SourceOrigin.COMPLETED_MATCH)
    compound = purchases[2]
    assert compound.destroy_order_reconciliation_used is True
    assert compound.inventory_before.owned(1042) == 2  # v6: antes da transação (v5: 0)
    assert compound.inventory_before.owned(5001) == 0  # nunca comprado à parte


def test_destroy_before_purchase_conflict_between_multiple_compound_purchases() -> None:
    """Duas compras compostas concorrendo pelo mesmo pool de destroys no
    mesmo instante -- primeiro-a-chegar (ordem real de source_index) leva,
    nunca reivindica a mesma unidade para as duas."""
    catalog = {
        1042: _item(1042),
        3035: _item(3035, recipe=[{"itemId": 1042, "quantity": 1}]),
        3036: _item(3036, recipe=[{"itemId": 1042, "quantity": 1}]),
        3037: _item(3037, recipe=[{"itemId": 1042, "quantity": 1}]),
    }
    events = [
        _event(0, 1000, "ITEM_PURCHASED", participantId=P1, itemId=1042),
        _event(1, 1001, "ITEM_PURCHASED", participantId=P1, itemId=1042),
        _event(2, 2000, "ITEM_DESTROYED", participantId=P1, itemId=1042),
        _event(3, 2000, "ITEM_DESTROYED", participantId=P1, itemId=1042),
        _event(4, 2000, "ITEM_PURCHASED", participantId=P1, itemId=3035),
        _event(5, 2000, "ITEM_PURCHASED", participantId=P1, itemId=3036),
        _event(6, 2000, "ITEM_PURCHASED", participantId=P1, itemId=3037),
    ]
    purchases = _reduce(events, catalog_items=catalog, origin=SourceOrigin.COMPLETED_MATCH)
    first, second, third = purchases[2], purchases[3], purchases[4]
    # destroy_order_reconciliation_used é CUMULATIVO (mesmo padrão de
    # destroy_reconciliation_heuristic_used) -- uma vez True, permanece True
    # nas compras seguintes mesmo quando ELAS não reivindicam nada. O sinal
    # específico de quantas vezes a reivindicação de fato ocorreu é a
    # contagem do reason code, que precisa ser exatamente 2 (nunca 3) --
    # prova que a terceira compra não inventou uma terceira unidade.
    assert first.destroy_order_reconciliation_used is True
    assert second.destroy_order_reconciliation_used is True
    assert third.destroy_order_reconciliation_used is True  # cumulativo, herdado
    claim_count = sum(
        1 for r in third.reasons if r.code == "UNVALIDATED_DESTROY_ORDER_RECONCILIATION"
    )
    assert claim_count == 2  # só as duas primeiras reivindicaram de verdade
    # Identidade por EVENTO, nunca por compra -- as
    # duas unidades destruídas (source_index 2 e 3) são reivindicadas uma
    # vez cada, nunca a mesma para as duas compras.
    assert first.destroy_order_claimed_source_indexes != second.destroy_order_claimed_source_indexes
    assert set(first.destroy_order_claimed_source_indexes) | set(
        second.destroy_order_claimed_source_indexes
    ) == {2, 3}
    assert third.destroy_order_claimed_source_indexes == ()  # nada sobrou para reivindicar


def test_destroy_before_purchase_isolated_per_participant() -> None:
    """Destroy de OUTRO participante no mesmo timestamp nunca é reivindicado
    -- reconstruct_participant_purchases filtra por dono antes de tudo, o
    pool é local a cada chamada."""
    P2 = 2
    catalog = {1042: _item(1042), 3035: _item(3035, recipe=[{"itemId": 1042, "quantity": 2}])}
    events = [
        _event(0, 1000, "ITEM_PURCHASED", participantId=P2, itemId=1042),
        _event(1, 1001, "ITEM_PURCHASED", participantId=P2, itemId=1042),
        _event(2, 2000, "ITEM_DESTROYED", participantId=P2, itemId=1042),
        _event(3, 2000, "ITEM_DESTROYED", participantId=P2, itemId=1042),
        _event(4, 2000, "ITEM_PURCHASED", participantId=P1, itemId=3035),
    ]
    purchases_p1 = _reduce(
        events, participant_id=P1, catalog_items=catalog, origin=SourceOrigin.COMPLETED_MATCH
    )
    assert purchases_p1[-1].destroy_order_reconciliation_used is False


def test_destroy_before_purchase_trinket_never_enters_backward_pool() -> None:
    """Destroy de trinket nunca alimenta o pool -- trinket não é receita."""
    catalog = {
        4001: _item(4001, tags=("Trinket",)),
        3035: _item(3035, recipe=[{"itemId": 4001, "quantity": 1}]),
    }
    events = [
        _event(0, 1000, "ITEM_PURCHASED", participantId=P1, itemId=4001),
        _event(1, 2000, "ITEM_DESTROYED", participantId=P1, itemId=4001),
        _event(2, 2000, "ITEM_PURCHASED", participantId=P1, itemId=3035),
    ]
    purchases = _reduce(events, catalog_items=catalog, origin=SourceOrigin.COMPLETED_MATCH)
    assert purchases[-1].destroy_order_reconciliation_used is False


def test_destroy_without_ownership_never_enters_backward_pool() -> None:
    """Destroy sem posse real (DESTROY_WITHOUT_OWNERSHIP) nunca alimenta o
    pool -- nada de real para redirecionar."""
    catalog = {1042: _item(1042), 3035: _item(3035, recipe=[{"itemId": 1042, "quantity": 1}])}
    events = [
        _event(0, 2000, "ITEM_DESTROYED", participantId=P1, itemId=1042),  # nunca possuído
        _event(1, 2000, "ITEM_PURCHASED", participantId=P1, itemId=3035),
    ]
    purchases = _reduce(events, catalog_items=catalog, origin=SourceOrigin.COMPLETED_MATCH)
    assert purchases[-1].destroy_order_reconciliation_used is False
    assert any(r.code == "DESTROY_WITHOUT_OWNERSHIP" for r in purchases[-1].reasons)


def test_destroy_before_purchase_never_crosses_timestamp_boundary() -> None:
    """Fronteira temporal provada: destroy em T1, compra que precisaria do
    componente em T2 > T1 -- pool é resetado a cada mudança de timestamp,
    nunca reivindicado além do mesmo instante."""
    catalog = {1042: _item(1042), 3035: _item(3035, recipe=[{"itemId": 1042, "quantity": 1}])}
    events = [
        _event(0, 1000, "ITEM_PURCHASED", participantId=P1, itemId=1042),
        _event(1, 2000, "ITEM_DESTROYED", participantId=P1, itemId=1042),
        _event(2, 2001, "ITEM_PURCHASED", participantId=P1, itemId=3035),  # timestamp DIFERENTE
    ]
    purchases = _reduce(events, catalog_items=catalog, origin=SourceOrigin.COMPLETED_MATCH)
    assert purchases[-1].destroy_order_reconciliation_used is False


def test_destroy_order_reconciliation_gates_eligibility_for_real_origin() -> None:
    catalog = {1042: _item(1042), 3035: _item(3035, recipe=[{"itemId": 1042, "quantity": 1}])}
    events = [
        _event(0, 1000, "ITEM_PURCHASED", participantId=P1, itemId=1042),
        _event(1, 2000, "ITEM_DESTROYED", participantId=P1, itemId=1042),
        _event(2, 2000, "ITEM_PURCHASED", participantId=P1, itemId=3035),
    ]
    purchases = _reduce(events, origin=SourceOrigin.COMPLETED_MATCH, catalog_items=catalog)
    final = purchases[-1]
    assert final.destroy_order_reconciliation_used is True
    assert final.uncertain is True
    assert any(r.code == "UNVALIDATED_DESTROY_ORDER_RECONCILIATION" for r in final.reasons)


def test_destroy_order_reconciliation_gate_stays_off_for_demo_origin() -> None:
    """Contraste direto: mesmo cenário, origin=CURATED_DEMO (default) -- o
    GATE específico desta heurística (UNVALIDATED_DESTROY_ORDER_
    RECONCILIATION) nunca dispara em DEMO, igual às outras duas.

    v6: o destroy reivindicado faz parte da transação da compra; a marca
    provisória UNRECONCILED_ITEM_DESTROYED dele sai do cumulativo (mesmo
    instante T), então em DEMO a compra fica SEM nenhuma marca. Na v5 este
    teste afirmava que a marca permanecia (`uncertain` True mesmo em DEMO), que
    era justamente o defeito corrigido pela v6."""
    catalog = {1042: _item(1042), 3035: _item(3035, recipe=[{"itemId": 1042, "quantity": 1}])}
    events = [
        _event(0, 1000, "ITEM_PURCHASED", participantId=P1, itemId=1042),
        _event(1, 2000, "ITEM_DESTROYED", participantId=P1, itemId=1042),
        _event(2, 2000, "ITEM_PURCHASED", participantId=P1, itemId=3035),
    ]
    purchases = _reduce(events, catalog_items=catalog)
    final = purchases[-1]
    assert final.destroy_order_reconciliation_used is True
    assert not any(r.code == "UNVALIDATED_DESTROY_ORDER_RECONCILIATION" for r in final.reasons)
    assert not any(r.code == "UNRECONCILED_ITEM_DESTROYED" for r in final.reasons)
    assert final.uncertain is False


def test_destroy_before_purchase_undo_restores_prior_state() -> None:
    """Undo da compra composta que reivindicou destroys anteriores restaura
    o snapshot exato anterior à TRANSAÇÃO dela (S4, v6): os componentes
    voltam. Na v5 o snapshot já vinha sem os componentes destruídos e o undo
    os deixava perdidos."""
    catalog = {1042: _item(1042), 3035: _item(3035, recipe=[{"itemId": 1042, "quantity": 1}])}
    events = [
        _event(0, 1000, "ITEM_PURCHASED", participantId=P1, itemId=1042),
        _event(1, 2000, "ITEM_DESTROYED", participantId=P1, itemId=1042),
        _event(2, 2000, "ITEM_PURCHASED", participantId=P1, itemId=3035),
        _event(3, 3000, "ITEM_UNDO", participantId=P1, beforeId=3035, afterId=0),
        _event(4, 4000, "ITEM_PURCHASED", participantId=P1, itemId=9999),
    ]
    catalog[9999] = _item(9999)
    purchases = _reduce(events, catalog_items=catalog, origin=SourceOrigin.COMPLETED_MATCH)
    final = purchases[-1]
    assert any(r.code == "UNVALIDATED_UNDO_SEMANTICS" for r in final.reasons)
    assert final.inventory_before.owned(3035) == 0
    assert final.inventory_before.owned(1042) == 1  # o undo restaura o componente (v5: 0)


def test_destroy_before_purchase_metamorphic_prior_purchase_is_preserved() -> None:
    """Prefixo fixo, futuro variado: a compra ANTERIOR aos destroys nunca
    muda, independente do que é comprado depois (checagem de sufixo/prefixo
    original, mantida ao lado da checagem da MESMA posição de decisão
    abaixo -- as duas propriedades são diferentes, uma não substitui a
    outra)."""
    catalog = {
        1042: _item(1042),
        3035: _item(3035, recipe=[{"itemId": 1042, "quantity": 2}]),
        9999: _item(9999),
    }
    prefix = [
        _event(0, 1000, "ITEM_PURCHASED", participantId=P1, itemId=1042),
        _event(1, 1001, "ITEM_PURCHASED", participantId=P1, itemId=1042),
        _event(2, 2000, "ITEM_DESTROYED", participantId=P1, itemId=1042),
        _event(3, 2000, "ITEM_DESTROYED", participantId=P1, itemId=1042),
    ]
    variant_a = [*prefix, _event(4, 2000, "ITEM_PURCHASED", participantId=P1, itemId=3035)]
    variant_b = [*prefix, _event(4, 2000, "ITEM_PURCHASED", participantId=P1, itemId=9999)]
    purchases_a = _reduce(variant_a, catalog_items=catalog, origin=SourceOrigin.COMPLETED_MATCH)
    purchases_b = _reduce(variant_b, catalog_items=catalog, origin=SourceOrigin.COMPLETED_MATCH)
    prior_a, prior_b = purchases_a[1], purchases_b[1]
    assert prior_a.inventory_before == prior_b.inventory_before
    assert prior_a.gold_estimate == prior_b.gold_estimate
    assert prior_a.uncertain == prior_b.uncertain
    assert prior_a.reasons == prior_b.reasons


def test_destroy_before_purchase_metamorphic_same_decision_position() -> None:
    """CRÍTICO, com o invariante CORRETO (v6): mesmo histórico
    ANTES do instante T e três alternativas em T -- (a) destroys dos
    componentes + compra do alvo, (b) compra do alvo sem destroys, (c) compra
    sem relação. inventory_before, qualidade PRÉ-decisão e ouro precisam ser
    idênticos. A versão anterior deste teste fixava um "prefixo" que já
    incluía os destroys causados pelo alvo (só o alvo mudava), então nunca
    detectou que o snapshot omitia a receita do próprio alvo; o caso "destroys
    presentes + compra sem relação" é outra coisa (S3, conservador) e tem
    teste próprio em test_reconstruction_v6.py."""
    catalog = {1042: _item(1042), 3035: _item(3035, recipe=[{"itemId": 1042, "quantity": 1}])}
    catalog[9999] = _item(9999)
    history = [_event(0, 1000, "ITEM_PURCHASED", participantId=P1, itemId=1042)]
    with_destroys = [
        *history,
        _event(1, 2000, "ITEM_DESTROYED", participantId=P1, itemId=1042),
        _event(2, 2000, "ITEM_PURCHASED", participantId=P1, itemId=3035),
    ]
    without_destroys = [*history, _event(1, 2000, "ITEM_PURCHASED", participantId=P1, itemId=3035)]
    unrelated = [*history, _event(1, 2000, "ITEM_PURCHASED", participantId=P1, itemId=9999)]
    for origin in (SourceOrigin.CURATED_DEMO, SourceOrigin.COMPLETED_MATCH):
        a, b, c = (
            _reduce(events, catalog_items=catalog, origin=origin)[-1]
            for events in (with_destroys, without_destroys, unrelated)
        )
        for other in (b, c):
            assert a.inventory_before == other.inventory_before
            assert a.gold_estimate == other.gold_estimate
            assert a.pre_decision_uncertain == other.pre_decision_uncertain
            assert a.pre_decision_reasons == other.pre_decision_reasons
        assert a.inventory_before.owned(1042) == 1 and not a.pre_decision_uncertain

    # divergência LEGÍTIMA, só pós-transição (dependente do alvo escolhido):
    real_a = _reduce(with_destroys, catalog_items=catalog, origin=SourceOrigin.COMPLETED_MATCH)[-1]
    real_c = _reduce(unrelated, catalog_items=catalog, origin=SourceOrigin.COMPLETED_MATCH)[-1]
    assert real_a.destroy_order_reconciliation_used is True
    assert real_c.destroy_order_reconciliation_used is False
    assert any(r.code == "UNVALIDATED_DESTROY_ORDER_RECONCILIATION" for r in real_a.reasons)
    assert not any(r.code == "UNVALIDATED_DESTROY_ORDER_RECONCILIATION" for r in real_c.reasons)


def test_pre_decision_quality_excludes_unknown_item_and_capacity_alerts() -> None:
    """Alertas de capacidade e de item desconhecido são calculados após
    aplicar o alvo -- não é só o código de destroy-order que é
    pós-transição; ITEM_UNKNOWN/INVENTORY_CAPACITY_EXCEEDED também
    dependem do item ESCOLHIDO e nunca devem aparecer em
    pre_decision_reasons."""
    catalog = {1042: _item(1042)}
    prefix = [
        _event(0, 1000, "ITEM_PURCHASED", participantId=P1, itemId=1042),
        _event(1, 2000, "ITEM_DESTROYED", participantId=P1, itemId=1042),
    ]
    variant_known = [*prefix, _event(2, 2000, "ITEM_PURCHASED", participantId=P1, itemId=1042)]
    variant_unknown = [*prefix, _event(2, 2000, "ITEM_PURCHASED", participantId=P1, itemId=7777)]
    purchases_known = _reduce(
        variant_known, catalog_items=catalog, origin=SourceOrigin.COMPLETED_MATCH
    )
    purchases_unknown = _reduce(
        variant_unknown, catalog_items=catalog, origin=SourceOrigin.COMPLETED_MATCH
    )
    target_known, target_unknown = purchases_known[-1], purchases_unknown[-1]

    assert target_known.pre_decision_uncertain == target_unknown.pre_decision_uncertain
    assert target_known.pre_decision_reasons == target_unknown.pre_decision_reasons
    assert not any(r.code == "ITEM_UNKNOWN" for r in target_known.pre_decision_reasons)
    assert not any(r.code == "ITEM_UNKNOWN" for r in target_unknown.pre_decision_reasons)

    # divergência pós-transição: só o item desconhecido dispara ITEM_UNKNOWN.
    assert any(r.code == "ITEM_UNKNOWN" for r in target_unknown.reasons)
    assert not any(r.code == "ITEM_UNKNOWN" for r in target_known.reasons)


def test_pre_decision_quality_flags_missing_catalog_from_first_purchase() -> None:
    """Catálogo ausente (catalog_id=None) é uma condição do PATCH da
    partida inteira, conhecida ANTES de qualquer evento -- nunca depende do
    item escolhido. A PRIMEIRA compra precisa refletir isso em
    pre_decision_uncertain/pre_decision_reasons, nunca só a partir do
    histórico acumulado (vazio na primeira compra, o que esconderia a
    condição já conhecida desde antes do replay)."""
    events = [_event(0, 1000, "ITEM_PURCHASED", participantId=P1, itemId=1001)]
    [first] = _reduce(events, origin=SourceOrigin.COMPLETED_MATCH, catalog_id=None)
    assert first.pre_decision_uncertain is True
    assert any(r.code == "NO_CATALOG" for r in first.pre_decision_reasons)
    # a versão cumulativa continua marcando o item específico, sem mudança.
    assert any(r.code == "NO_CATALOG" and r.item_id == 1001 for r in first.reasons)
    # a versão pré-decisão não atribui o código a um item específico -- é
    # uma condição do patch/partida, não do alvo.
    assert any(r.code == "NO_CATALOG" and r.item_id is None for r in first.pre_decision_reasons)


def test_pre_decision_quality_missing_catalog_independent_of_target() -> None:
    """Mesmo prefixo (catálogo ausente desde o início), dois alvos
    alternativos na primeira decisão -- pre_decision_uncertain/
    pre_decision_reasons idênticos, apesar de purchased_item_id divergir."""
    events_b = [_event(0, 1000, "ITEM_PURCHASED", participantId=P1, itemId=1001)]
    events_c = [_event(0, 1000, "ITEM_PURCHASED", participantId=P1, itemId=9999)]
    [first_b] = _reduce(events_b, origin=SourceOrigin.COMPLETED_MATCH, catalog_id=None)
    [first_c] = _reduce(events_c, origin=SourceOrigin.COMPLETED_MATCH, catalog_id=None)
    assert first_b.pre_decision_uncertain == first_c.pre_decision_uncertain is True
    assert first_b.pre_decision_reasons == first_c.pre_decision_reasons
    assert first_b.purchased_item_id != first_c.purchased_item_id


def test_pre_decision_quality_persisted_projection_flags_missing_catalog() -> None:
    """A projeção persistida (_decision_state_from_purchase) precisa
    refletir o mesmo gate desde a primeira compra -- quality.
    preDecisionQuality não pode ficar {"uncertain": False, "reasons": []}
    quando o catálogo já é sabidamente ausente."""
    from riftlab.timeline.application import _decision_state_from_purchase

    events = [_event(0, 1000, "ITEM_PURCHASED", participantId=P1, itemId=1001)]
    [first] = _reduce(events, origin=SourceOrigin.COMPLETED_MATCH, catalog_id=None)
    state = _decision_state_from_purchase(
        match_id=MATCH_ID, catalog_id=None, origin=SourceOrigin.COMPLETED_MATCH, purchase=first
    )
    pre_quality = state.quality["preDecisionQuality"]
    assert pre_quality["uncertain"] is True
    assert any(r["code"] == "NO_CATALOG" for r in pre_quality["reasons"])
