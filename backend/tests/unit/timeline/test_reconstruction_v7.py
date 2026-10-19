"""Reconstrução v7 (CC-207): regras EMPÍRICAS do reducer, desligadas por padrão.

Cobre, sem I/O: (1) regressão v6 BIT A BIT com as regras desligadas (digests gerados
pelo reducer v6 do HEAD 2575e8a); (2) propriedades metamórficas com as regras LIGADAS
(pré-decisão independente do alvo; invariância do prefixo); (3) cada regra na forma
APROVADA (sem marca) e nas formas REPROVADAS (a marca continua); (4) proveniência.
Sequências sintéticas: não são partidas reais nem provam semântica oficial da Riot; as
formas foram observadas na exploração pré-registrada do CC-207 (patches 16.16/16.17)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from riftlab.shared.domain import SourceOrigin
from riftlab.timeline.application import (
    RECONSTRUCTION_ALGORITHM_VERSION,
    ReconstructedPurchase,
    _decision_state_from_purchase,
    reconstruct_participant_purchases,
)
from riftlab.timeline.domain import TimelineEvent
from riftlab.timeline.empirical_rules import (
    RULE_R1,
    RULE_R2,
    RULE_R2I,
    RULE_R4S,
    RULE_R5,
    RULE_R6,
)
from support.reducer_scenarios import (
    CATALOG,
    CID,
    FRAMES,
    IE,
    ONE_A,
    ORIGINS,
    P1,
    POTION,
    QUEST,
    RECALL,
    SYSTEM_IDS,
    TRK_A,
    TRK_B,
    TRK_C,
    A,
    B,
    battery,
    buy,
    destroy,
    digest,
    sell,
    undo,
)

DEMO, REAL = SourceOrigin.CURATED_DEMO, SourceOrigin.COMPLETED_MATCH
GOLDEN = Path(__file__).parent / "golden" / "reducer_v6_digests.json"
ALL_RULES = frozenset({RULE_R1, RULE_R2, RULE_R2I, RULE_R4S, RULE_R5, RULE_R6})
T = 60_000


def _reduce(
    events: list[TimelineEvent],
    rules: frozenset[str] = frozenset(),
    origin: SourceOrigin = REAL,
    system_item_ids: frozenset[int] = SYSTEM_IDS,
    participant: int = P1,
) -> list[ReconstructedPurchase]:
    return reconstruct_participant_purchases(
        participant_id=participant,
        events=events,
        origin=origin,
        frames=FRAMES,
        catalog_id=CID,
        catalog_items=CATALOG,
        enabled_rules=rules,
        system_item_ids=system_item_ids,
    )


def _codes(reasons: tuple[Any, ...]) -> list[str]:
    return [reason.code for reason in reasons]


def _all_codes(purchases: list[ReconstructedPurchase]) -> set[str]:
    return {code for purchase in purchases for code in _codes(purchase.reasons)}


# ---------------------------------------------------------------------------
# 1. regressão v6 bit a bit, com as regras desligadas
# ---------------------------------------------------------------------------


def test_algorithm_version_is_thirteen() -> None:
    # v13: regras da CC-1002 nos quatro domínios 16.19/16.20 × 420/440, sobre as do CC-316,
    # CC-324 e CC-802; patches seguintes sem confirmação própria herdam com aviso.
    assert RECONSTRUCTION_ALGORITHM_VERSION == 13


def test_rules_off_reproduce_the_v6_reducer_bit_for_bit() -> None:
    golden = json.loads(GOLDEN.read_text())
    scenarios = battery()
    assert golden["scenarioCount"] == len(scenarios) == len(golden["digests"])
    divergent: list[str] = []
    for name, events in scenarios.items():
        for label, origin in ORIGINS.items():
            purchases = _reduce(events, frozenset(), origin, frozenset())
            if digest(purchases) != golden["digests"][name][label]:
                divergent.append(f"{name}/{label}")
    assert divergent == []


def test_default_arguments_equal_explicit_empty_rules() -> None:
    for name, events in list(battery().items())[:60]:
        implicit = reconstruct_participant_purchases(
            participant_id=P1,
            events=events,
            origin=REAL,
            frames=FRAMES,
            catalog_id=CID,
            catalog_items=CATALOG,
        )
        assert digest(implicit) == digest(_reduce(events, frozenset(), REAL, frozenset())), name


def test_system_item_ids_without_the_rule_change_nothing() -> None:
    events = [destroy(0, 30_000, QUEST), buy(1, 50_000, A)]
    assert digest(_reduce(events, frozenset(), REAL, SYSTEM_IDS)) == digest(
        _reduce(events, frozenset(), REAL, frozenset())
    )


# ---------------------------------------------------------------------------
# 2. metamórficos com as regras LIGADAS
# ---------------------------------------------------------------------------


def _pre_decision(purchase: ReconstructedPurchase) -> tuple[Any, ...]:
    return (
        purchase.event_source_index,
        purchase.purchased_item_id,
        purchase.inventory_before,
        purchase.pre_decision_uncertain,
        purchase.pre_decision_reasons,
    )


@pytest.mark.parametrize(
    "rules", [frozenset(), ALL_RULES], ids=["regras_desligadas", "regras_ligadas"]
)
def test_perturbing_events_after_a_cut_never_changes_earlier_pre_decision_states(
    rules: frozenset[str],
) -> None:
    checked = 0
    for name, events in battery().items():
        full = _reduce(events, rules)
        for cut in {len(events) // 3, len(events) // 2, len(events) - 1}:
            if cut <= 0:
                continue
            truncated = _reduce(events[:cut], rules)
            earlier = [p for p in full if p.event_source_index < cut]
            assert [_pre_decision(p) for p in earlier] == [_pre_decision(p) for p in truncated], (
                f"{name} corte {cut}"
            )
            checked += 1
    assert checked > 200


@pytest.mark.parametrize(
    "rules", [frozenset(), ALL_RULES], ids=["regras_desligadas", "regras_ligadas"]
)
def test_pre_decision_never_depends_on_the_target_of_the_purchase(rules: frozenset[str]) -> None:
    """Mesmo histórico antes de T e alvos diferentes em T: a pré-decisão é a mesma (a transação
    destroy→compra faz parte do alvo, não do histórico)."""
    history = [buy(0, 1000, A), buy(1, 2000, B), buy(2, 3000, POTION), buy(3, 4000, TRK_B)]
    variants = {
        "destroys+IE (forma real)": [*history, destroy(4, T, A), destroy(5, T, B), buy(6, T, IE)],
        "IE sem destroys": [*history, buy(4, T, IE)],
        "alvo sem relação": [*history, buy(4, T, ONE_A)],
        "alvo trinket": [*history, buy(4, T, TRK_C)],
    }
    seen = {
        label: _pre_decision(_reduce(events, rules)[-1])[3:] for label, events in variants.items()
    }
    assert len({repr(value) for value in seen.values()}) == 1, seen


@pytest.mark.parametrize(
    "rules", [frozenset(), ALL_RULES], ids=["regras_desligadas", "regras_ligadas"]
)
def test_snapshots_of_a_result_are_frozen_values(rules: frozenset[str]) -> None:
    events = [buy(0, 1000, A), buy(1, 2000, B), buy(2, 3000, ONE_A)]
    first = _reduce(events, rules)
    assert digest(first) == digest(_reduce(events, rules))


# ---------------------------------------------------------------------------
# 3. R5 — libera UNVALIDATED_DESTROY_ORDER_RECONCILIATION
# ---------------------------------------------------------------------------

REAL_FORM = [buy(0, 1000, A), buy(1, 2000, B), destroy(2, T, A), destroy(3, T, B), buy(4, T, IE)]


def test_r5_real_form_is_released_only_with_the_rule() -> None:
    off = _reduce(REAL_FORM)[-1]
    on = _reduce(REAL_FORM, frozenset({RULE_R5}))[-1]
    assert _codes(off.reasons) == ["UNVALIDATED_DESTROY_ORDER_RECONCILIATION"]
    assert on.reasons == () and on.uncertain is False
    assert on.inventory_before == off.inventory_before  # o snapshot não muda


def test_r5_never_changes_the_demo_origin() -> None:
    assert digest(_reduce(REAL_FORM, frozenset({RULE_R5}), DEMO)) == digest(
        _reduce(REAL_FORM, frozenset(), DEMO)
    )


def test_r5_does_not_release_the_forward_form_or_unclaimed_destroys() -> None:
    # as marcas dos destroys chegam DEPOIS da compra composta: uma compra seguinte as expõe
    forward = [
        buy(0, 1000, A), buy(1, 2000, B), buy(2, T, IE), destroy(3, T, A), destroy(4, T, B),
        buy(5, T + 5000, ONE_A),
    ]  # fmt: skip
    forward_codes = _all_codes(_reduce(forward, frozenset({RULE_R5})))
    assert "UNVALIDATED_DESTROY_RECONCILIATION" in forward_codes  # forma reprovada: marca continua
    excess = [
        buy(0, 1000, A),
        buy(1, 1500, A),
        destroy(2, T, A),
        destroy(3, T, A),
        buy(4, T, ONE_A),
    ]
    excess_codes = _all_codes(_reduce(excess, frozenset({RULE_R5})))
    assert "UNRECONCILED_ITEM_DESTROYED" in excess_codes  # o destroy que sobra não é reivindicado


# ---------------------------------------------------------------------------
# 4. R1 — consumo de unidade POSSUÍDA
# ---------------------------------------------------------------------------


def test_r1_owned_consumable_destroy_is_consumption_without_a_mark() -> None:
    events = [buy(0, 1000, POTION), destroy(1, 20_000, POTION), buy(2, 30_000, A)]
    off = _reduce(events)[-1]
    on = _reduce(events, frozenset({RULE_R1}))[-1]
    assert _codes(off.reasons) == ["UNRECONCILED_ITEM_DESTROYED"]
    assert on.reasons == () and on.uncertain is False
    assert on.inventory_before.items == ()  # a unidade saiu do inventário (igual à v6)
    assert on.inventory_before == off.inventory_before


def test_r1_consumes_exactly_one_unit() -> None:
    events = [
        buy(0, 1000, POTION),
        buy(1, 1100, POTION),
        destroy(2, 20_000, POTION),
        buy(3, 30_000, A),
    ]
    target = _reduce(events, frozenset({RULE_R1}))[-1]
    assert target.reasons == ()
    assert dict(target.inventory_before.items) == {POTION: 1}


def test_r1_consumable_without_ownership_keeps_both_marks() -> None:
    events = [destroy(0, 20_000, POTION), buy(1, 30_000, A)]
    target = _reduce(events, frozenset({RULE_R1}))[-1]
    assert _codes(target.reasons) == ["UNRECONCILED_ITEM_DESTROYED", "DESTROY_WITHOUT_OWNERSHIP"]


def test_r1_never_releases_a_non_consumable_owned_destroy() -> None:
    events = [buy(0, 1000, A), destroy(1, 20_000, A), buy(2, 30_000, B)]
    target = _reduce(events, frozenset({RULE_R1}))[-1]
    assert _codes(target.reasons) == ["UNRECONCILED_ITEM_DESTROYED"]


def test_r1_does_not_interfere_with_a_recipe_claim() -> None:
    events = [buy(0, 1000, A), destroy(1, T, A), buy(2, T, ONE_A)]
    assert digest(_reduce(events, frozenset({RULE_R1}))) == digest(_reduce(events))


# ---------------------------------------------------------------------------
# 5. R2 / R2i — troca de trinket e trinket inicial
# ---------------------------------------------------------------------------


def test_r2_explicit_swap_destroy_before_purchase_is_not_a_mark() -> None:
    events = [buy(0, 1000, TRK_A), destroy(1, T, TRK_A), buy(2, T, TRK_B), buy(3, 90_000, A)]
    off = _reduce(events)[-1]
    on = _reduce(events, frozenset({RULE_R2}))[-1]
    assert "UNRECONCILED_ITEM_DESTROYED" in _codes(off.reasons)
    assert on.reasons == ()
    assert on.inventory_before.trinket_item_id == TRK_B  # a slot trocou


def test_r2_swap_of_the_implicit_default_needs_r2i() -> None:
    events = [destroy(0, T, TRK_A), buy(1, T, TRK_B), buy(2, 90_000, A)]
    only_swap = _reduce(events, frozenset({RULE_R2}))[-1]
    both = _reduce(events, frozenset({RULE_R2, RULE_R2I}))[-1]
    assert "DESTROY_WITHOUT_OWNERSHIP" in _codes(only_swap.reasons)  # sem o padrão no livro
    assert both.reasons == () and both.inventory_before.trinket_item_id == TRK_B


def test_r2_destroy_of_a_trinket_without_a_co_temporal_trinket_purchase_still_marks() -> None:
    events = [buy(0, 1000, TRK_A), destroy(1, T, TRK_A), buy(2, 90_000, A)]
    target = _reduce(events, frozenset({RULE_R2, RULE_R2I}))[-1]
    assert "UNRECONCILED_ITEM_DESTROYED" in _codes(target.reasons)


def test_r2_destroy_of_a_different_trinket_than_the_owned_one_still_marks() -> None:
    events = [buy(0, 1000, TRK_A), destroy(1, T, TRK_B), buy(2, T, TRK_C), buy(3, 90_000, A)]
    target = _reduce(events, frozenset({RULE_R2, RULE_R2I}))[-1]
    assert "UNRECONCILED_ITEM_DESTROYED" in _codes(target.reasons)


def test_r2i_start_replacement_without_destroy_is_not_a_mark() -> None:
    events = [buy(0, 8_000, TRK_C), buy(1, 90_000, A)]
    target = _reduce(events, frozenset({RULE_R2I}))[-1]
    assert target.reasons == () and target.inventory_before.trinket_item_id == TRK_C


def test_r2i_puts_the_default_trinket_in_the_first_snapshot() -> None:
    events = [buy(0, 5_000, A)]
    assert _reduce(events)[0].inventory_before.trinket_item_id is None
    assert _reduce(events, frozenset({RULE_R2I}))[0].inventory_before.trinket_item_id == TRK_A


# ---------------------------------------------------------------------------
# 6. R4s — itens de sistema/missão
# ---------------------------------------------------------------------------


def test_r4s_system_item_without_ownership_changes_nothing_and_marks_nothing() -> None:
    events = [destroy(0, 30_000, QUEST), destroy(1, 40_000, RECALL), buy(2, 50_000, A)]
    off = _reduce(events)[-1]
    on = _reduce(events, frozenset({RULE_R4S}))[-1]
    assert {"UNRECONCILED_ITEM_DESTROYED", "DESTROY_WITHOUT_OWNERSHIP"} <= set(_codes(off.reasons))
    assert on.reasons == () and on.inventory_before.items == ()


def test_r4s_needs_the_item_list_from_the_registry() -> None:
    events = [destroy(0, 30_000, QUEST), buy(1, 50_000, A)]
    target = _reduce(events, frozenset({RULE_R4S}), REAL, frozenset())[-1]
    assert "DESTROY_WITHOUT_OWNERSHIP" in _codes(target.reasons)


def test_r4s_an_owned_system_item_follows_the_normal_path() -> None:
    events = [buy(0, 1000, QUEST), destroy(1, 30_000, QUEST), buy(2, 50_000, A)]
    target = _reduce(events, frozenset({RULE_R4S}))[-1]
    assert _codes(target.reasons) == [
        "UNRECONCILED_ITEM_DESTROYED"
    ]  # posse por compra: não é sistema


def test_r4s_an_item_outside_the_list_without_ownership_still_marks() -> None:
    events = [destroy(0, 30_000, A), buy(1, 50_000, B)]
    target = _reduce(events, frozenset({RULE_R4S}))[-1]
    assert "DESTROY_WITHOUT_OWNERSHIP" in _codes(target.reasons)


# ---------------------------------------------------------------------------
# 7. R6 — undo em pilha
# ---------------------------------------------------------------------------


def test_r6_two_level_undo_correlates_both_and_restores_the_inventory() -> None:
    events = [
        buy(0, 1000, A),
        buy(1, 1100, B),
        undo(2, 1200, B, 0),
        undo(3, 1300, A, 0),
        buy(4, 5000, ONE_A),
    ]
    off = _reduce(events)
    on = _reduce(events, frozenset({RULE_R6}))
    assert "UNDO_UNCORRELATED" in _all_codes(off)  # 1 nível: o 2º undo não correlaciona
    target = on[-1]
    assert target.reasons == () and target.inventory_before.items == ()
    assert [p.retrospective_status for p in on[:2]] == ["REVERTED_BY_UNDO", "REVERTED_BY_UNDO"]


def test_r6_undo_of_a_composite_transaction_then_of_the_previous_sale() -> None:
    events = [
        buy(0, 500, A), buy(1, 600, B), sell(2, 700, A), destroy(3, T, B), buy(4, T, ONE_A),
        undo(5, T + 1000, ONE_A, 0), undo(6, T + 2000, 0, A), buy(7, T + 3000, A),
    ]  # fmt: skip
    off_codes = _all_codes(_reduce(events, frozenset({RULE_R5})))
    on_codes = _all_codes(_reduce(events, frozenset({RULE_R5, RULE_R6})))
    assert "UNDO_UNCORRELATED" in off_codes  # o destroy da transação limpava a pilha na v6
    assert "UNDO_UNCORRELATED" not in on_codes and "UNVALIDATED_UNDO_SEMANTICS" not in on_codes


def test_r6_uncorrelated_undo_still_marks() -> None:
    events = [buy(0, 1000, A), undo(1, 1500, B, 0), buy(2, 5000, B)]
    assert "UNDO_UNCORRELATED" in _all_codes(_reduce(events, frozenset({RULE_R6})))


def test_r6_an_unrelated_destroy_clears_the_stack() -> None:
    """Destroy que NÃO é parte de uma compra no mesmo instante (consumo) encerra a pilha."""
    events = [
        buy(0, 1000, A),
        buy(1, 2000, POTION),
        destroy(2, 3000, POTION),
        undo(3, 4000, POTION, 0),
        buy(4, 9000, B),
    ]
    codes = _all_codes(_reduce(events, frozenset({RULE_R1, RULE_R6})))
    assert "UNDO_UNCORRELATED" in codes


def test_r6_releases_the_undo_gate_for_real_but_never_touches_demo() -> None:
    events = [buy(0, 1000, A), undo(1, 1500, A, 0), buy(2, 5000, B)]
    assert "UNVALIDATED_UNDO_SEMANTICS" in _all_codes(_reduce(events))
    assert "UNVALIDATED_UNDO_SEMANTICS" not in _all_codes(_reduce(events, frozenset({RULE_R6})))
    assert digest(_reduce(events, frozenset({RULE_R6}), DEMO)) == digest(
        _reduce(events, frozenset(), DEMO)
    )


# ---------------------------------------------------------------------------
# 8. proveniência e isolamento entre participantes
# ---------------------------------------------------------------------------


def test_state_records_empirical_rules_provenance() -> None:
    target = _reduce([buy(0, 1000, A)])[-1]
    default_state = _decision_state_from_purchase("M", CID, REAL, target)
    assert default_state.state_version == 13 and default_state.quality["empiricalRules"] == []
    provenance = [{"ruleId": "R5", "ruleVersion": 1, "status": "EMPIRICALLY_SUPPORTED"}]
    state = _decision_state_from_purchase("M", CID, REAL, target, provenance)
    assert state.quality["empiricalRules"] == provenance
    provenance[0]["ruleId"] = "alterado"
    assert state.quality["empiricalRules"][0]["ruleId"] == "R5"  # cópia, não referência


def test_rules_never_leak_between_participants() -> None:
    """O consumo do participante 6 não altera o estado do participante 1."""
    events = [buy(0, 1000, POTION, 6), destroy(1, 20_000, POTION, 6), buy(2, 30_000, A)]
    only_p1 = _reduce(events, ALL_RULES, participant=P1)
    assert [p.purchased_item_id for p in only_p1] == [A]
    assert only_p1[0].reasons == () and only_p1[0].inventory_before.items == ()
    assert only_p1[0].inventory_before.trinket_item_id == TRK_A  # R2i vale para cada participante
