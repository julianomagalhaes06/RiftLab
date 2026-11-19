"""Decisões de late game para qualquer campeão (CC-1502). Sem banco, sem rede.

Uma decisão = o k-ésimo item principal (k a partir de 3, botas fora da contagem), ancorada no
compromisso (`decisions.commitment`): a primeira compra depois do item principal anterior. Os
candidatos legais e o contexto vêm da âncora; o alvo é o item principal completado depois. O
treino (corpus bruto) e o produto (estados do banco) chamam `late_decisions` com os mesmos
estados e pontos, então a âncora, a tolerância e os candidatos são idênticos nos dois caminhos.

Diferenças declaradas em relação ao CC-701 (`dataset/late.py`, experimental): qualquer campeão e
rota, ordem até 6, incerteza tolerada só para botas/consumíveis/trinket (`decisions.tolerance`) e
âncora permitida no mesmo instante do alvo (compra do item inteiro de uma vez; o inventário
antes da compra não revela o alvo e o ouro não é usado).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from riftlab.candidates.domain import (
    CandidateContext,
    CandidateInputError,
    CandidateResult,
    ItemRules,
    enumerate_candidates,
)
from riftlab.catalog.classification import ItemClass, ItemClassificationTable
from riftlab.catalog.domain import CatalogVersion, Item
from riftlab.decisions.commitment import (
    CompletionAssessment,
    CompletionKind,
    assess_completion,
    commitment_anchor,
    path_start,
)
from riftlab.decisions.domain import (
    REASON_TARGET_NOT_CLASSIFIABLE,
    REASON_TARGET_REVERTED,
    REASON_TARGET_STATUS_UNCERTAIN,
)
from riftlab.decisions.features import build_pre_decision_view
from riftlab.decisions.selection import DecisionPoint
from riftlab.decisions.tolerance import ToleratedView, tolerate
from riftlab.timeline.domain import DecisionState

LATE_ROWS_VERSION = "late_rows_v2"
MIN_LATE_ORDINAL = 3
MAX_LATE_ORDINAL = 6
_TARGET_CODES = frozenset(
    {REASON_TARGET_NOT_CLASSIFIABLE, REASON_TARGET_REVERTED, REASON_TARGET_STATUS_UNCERTAIN}
)


@dataclass(frozen=True, slots=True)
class LateDecision:
    target_state: DecisionState
    target_item_id: int
    anchor_state: DecisionState
    anchor: ToleratedView
    ordinal: int
    owned_main_item_ids: tuple[int, ...]
    candidates: CandidateResult
    target_legal: bool
    completion: CompletionAssessment
    path_start_state: DecisionState | None


def _completed_main(point: DecisionPoint | None) -> bool:
    """Item principal completo cuja compra não foi desfeita nem tem status incerto."""
    return (
        point is not None
        and point.target_class is ItemClass.MAIN
        and not any(reason.code in _TARGET_CODES for reason in point.reasons)
    )


def _main_units(view_items: Sequence[tuple[int, int]], table: ItemClassificationTable) -> list[int]:
    return sorted(
        item_id
        for item_id, count in view_items
        if table.classify(item_id).item_class is ItemClass.MAIN
        for _ in range(count)
    )


def candidates_at(
    tolerated: ToleratedView,
    *,
    champion_id: int,
    patch: str,
    map_id: int,
    catalog: CatalogVersion,
    items: tuple[Item, ...],
    classification: ItemClassificationTable,
    rules: ItemRules,
) -> CandidateResult:
    return enumerate_candidates(
        CandidateContext(tolerated.view, patch, map_id, champion_id),
        catalog,
        items,
        classification,
        rules,
    )


def late_decisions(
    states: Sequence[DecisionState],
    points: Sequence[DecisionPoint | None],
    *,
    champion_id: int,
    patch: str,
    map_id: int,
    catalog: CatalogVersion,
    items: tuple[Item, ...],
    classification: ItemClassificationTable,
    rules: ItemRules,
    min_ordinal: int = MIN_LATE_ORDINAL,
    max_ordinal: int = MAX_LATE_ORDINAL,
) -> tuple[list[LateDecision], Counter[str]]:
    """Decisões de um participante. `points[i]` é a avaliação de `states[i]` (None quando o
    alvo não é item principal). Nenhuma decisão é forçada: cada perda vira um código."""
    if len(states) != len(points):
        raise ValueError("estados e pontos desalinhados")
    order = sorted(range(len(states)), key=lambda i: states[i].event_source_index)
    states = [states[i] for i in order]
    points = [points[i] for i in order]
    by_id = {item.item_id: item for item in items}
    prices = {item.item_id: item.price for item in items}
    counts: Counter[str] = Counter()
    out: list[LateDecision] = []
    previous: int | None = None
    for state, point in zip(states, points, strict=True):
        if point is None or point.target_class is not ItemClass.MAIN:
            continue
        if not _completed_main(point):
            counts["TARGET_NOT_COMPLETED"] += 1
            continue
        prior, previous = previous, state.event_source_index
        anchor = commitment_anchor(
            states, target_index=state.event_source_index, previous_main=prior
        )
        if anchor is None:
            counts["NO_ANCHOR"] += 1
            continue
        tolerated = tolerate(build_pre_decision_view(anchor), classification)
        if tolerated is None:
            counts["ANCHOR_UNCERTAIN"] += 1
            continue
        owned = _main_units(tolerated.view.inventory_items, classification)
        ordinal = len(owned) + 1
        if not min_ordinal <= ordinal <= max_ordinal:
            counts[f"ORDINAL_{ordinal}_OUTSIDE"] += 1
            continue
        counts[f"ORDINAL_{ordinal}_ANCHORED"] += 1
        try:
            result = candidates_at(
                tolerated,
                champion_id=champion_id,
                patch=patch,
                map_id=map_id,
                catalog=catalog,
                items=items,
                classification=classification,
                rules=rules,
            )
        except CandidateInputError as exc:
            counts[f"CANDIDATES_{exc.code}"] += 1
            continue
        legal = point.target_item_id in {c.item_id for c in result.candidates}
        if not legal:
            counts["TARGET_NOT_LEGAL_AT_ANCHOR"] += 1
        completion = _completion(
            state,
            point.target_item_id,
            classification,
            prices,
            champion_id,
            patch,
            map_id,
            catalog,
            items,
            rules,
        )
        out.append(
            LateDecision(
                target_state=state,
                target_item_id=point.target_item_id,
                anchor_state=anchor,
                anchor=tolerated,
                ordinal=ordinal,
                owned_main_item_ids=tuple(owned),
                candidates=result,
                target_legal=legal,
                completion=completion,
                path_start_state=path_start(
                    states,
                    target_index=state.event_source_index,
                    target_item_id=point.target_item_id,
                    previous_main=prior,
                    items=by_id,
                ),
            )
        )
        counts[f"ORDINAL_{ordinal}_ROWS"] += 1
        if tolerated.tolerated:
            counts["ANCHOR_TOLERATED"] += 1
    return out, counts


def _completion(
    state: DecisionState,
    target: int,
    classification: ItemClassificationTable,
    prices: Mapping[int, int],
    champion_id: int,
    patch: str,
    map_id: int,
    catalog: CatalogVersion,
    items: tuple[Item, ...],
    rules: ItemRules,
) -> CompletionAssessment:
    tolerated = tolerate(build_pre_decision_view(state), classification)
    if tolerated is None:
        return CompletionAssessment(CompletionKind.UNKNOWN)
    try:
        result = candidates_at(
            tolerated,
            champion_id=champion_id,
            patch=patch,
            map_id=map_id,
            catalog=catalog,
            items=items,
            classification=classification,
            rules=rules,
        )
    except CandidateInputError:
        return CompletionAssessment(CompletionKind.UNKNOWN)
    return assess_completion(target, result.candidates, prices)


def decision_row(
    decision: LateDecision,
    *,
    match_id: str,
    split: str,
    participant_id: int,
    champion_id: int,
    role: str | None,
    context: Mapping[str, Any],
    win: bool | None,
) -> dict[str, Any]:
    """Linha privada de treino/avaliação (tem matchId). `win` é só para pesquisa de resultado
    (P3), nunca feature."""
    anchor = decision.anchor.view
    return {
        "schemaVersion": LATE_ROWS_VERSION,
        "matchId": match_id,
        "split": split,
        "participantId": participant_id,
        "championId": champion_id,
        "role": role,
        "ordinal": decision.ordinal,
        "targetItemId": decision.target_item_id,
        "targetTimestampMs": decision.target_state.snapshot.get("eventTimestampMs"),
        "anchorEventSourceIndex": decision.anchor_state.event_source_index,
        "anchorTimestampMs": anchor.event_timestamp_ms,
        "anchorIsTarget": decision.anchor_state.event_source_index
        == decision.target_state.event_source_index,
        "anchorTolerated": decision.anchor.tolerated,
        "level": anchor.level.value,
        "ownedMainItemIds": list(decision.owned_main_item_ids),
        "completion": decision.completion.to_payload(),
        "candidates": [
            {
                "itemId": c.item_id,
                "remainingCost": c.remaining_cost,
                "ownedComponent": "OWNED_COMPONENT_COMPATIBLE" in c.evidence_codes,
            }
            for c in decision.candidates.candidates
        ],
        "targetLegal": decision.target_legal,
        "context": dict(context),
        "win": win,
    }


__all__ = [
    "LATE_ROWS_VERSION",
    "MAX_LATE_ORDINAL",
    "MIN_LATE_ORDINAL",
    "LateDecision",
    "candidates_at",
    "decision_row",
    "late_decisions",
]
