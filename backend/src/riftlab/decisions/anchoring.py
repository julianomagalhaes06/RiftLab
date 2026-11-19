"""Seleção v3 (CC-1501): cada item principal é decidido no compromisso, não no instante final.

`evaluate_decision` continua avaliando cada compra no instante em que ela acontece (seleção
retrospectiva, motivos ligados ao alvo). Para cada item principal completo a partir do 2º (o 1º
não tem item anterior: a âncora seria o início da partida), este módulo troca
a parte de CONTEXTO da avaliação pela âncora de compromisso (`decisions.commitment`):
inventário, ordem do item, orçamento, features e recorte vêm da primeira compra depois do item
principal anterior. A incerteza do inventário da âncora pode ser tolerada só para botas,
consumíveis e trinket (`decisions.tolerance`, validada no CC-1502). Do 3º item em diante o
recorte não restringe campeão nem rota (recorte v4).

Os motivos ligados ao ALVO (classe, desfeito, status incerto) e o catálogo ausente vêm da
avaliação original; o resto é recalculado na âncora. A identidade do ponto (estado, evento,
instante) continua a da compra, para a linha do banco seguir ligada ao seu estado.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Any

from riftlab.catalog.classification import ItemClass, ItemClassificationTable
from riftlab.decisions.commitment import commitment_anchor
from riftlab.decisions.domain import (
    REASON_INVENTORY_UNCERTAIN,
    REASON_MISSING_CATALOG,
    REASON_OUT_OF_SCOPE,
    REASON_TARGET_NOT_CLASSIFIABLE,
    REASON_TARGET_REVERTED,
    REASON_TARGET_STATUS_UNCERTAIN,
    EligibilityReason,
    MatchContext,
    ParticipantView,
)
from riftlab.decisions.features import build_pre_decision_view, extract_features
from riftlab.decisions.scope import DecisionScope
from riftlab.decisions.selection import DecisionPoint, assess_budget
from riftlab.decisions.tolerance import TOLERANCE_VERSION, tolerate
from riftlab.timeline.domain import DecisionState

MAX_MAIN_ORDINAL = 6
_TARGET_CODES = frozenset(
    {REASON_TARGET_NOT_CLASSIFIABLE, REASON_TARGET_REVERTED, REASON_TARGET_STATUS_UNCERTAIN}
)
_UNCLASSIFIABLE = frozenset({ItemClass.UNKNOWN, ItemClass.EXCLUDED, ItemClass.UNCLASSIFIED})


@dataclass(frozen=True, slots=True)
class AnchorInfo:
    state_id: uuid.UUID
    event_source_index: int
    timestamp_ms: int
    tolerated: bool
    removed_item_ids: tuple[int, ...]

    def to_payload(self) -> dict[str, Any]:
        return {
            "stateId": str(self.state_id),
            "eventSourceIndex": self.event_source_index,
            "timestampMs": self.timestamp_ms,
            "tolerated": self.tolerated,
            "removedItemIds": list(self.removed_item_ids),
            "toleranceVersion": TOLERANCE_VERSION,
        }


def _completed_main(point: DecisionPoint) -> bool:
    return point.target_class is ItemClass.MAIN and not any(
        r.code in _TARGET_CODES for r in point.reasons
    )


def anchor_points(
    states: Sequence[DecisionState],
    points: Sequence[DecisionPoint | None],
    *,
    participants: tuple[ParticipantView, ...],
    match: MatchContext,
    classification: ItemClassificationTable | None,
    scope: DecisionScope,
) -> list[tuple[DecisionPoint, AnchorInfo | None]]:
    """Pontos de UM participante, na ordem dos estados, com a âncora de cada item principal."""
    if len(states) != len(points):
        raise ValueError("estados e pontos desalinhados")
    pairs = sorted(zip(states, points, strict=True), key=lambda sp: sp[0].event_source_index)
    ordered_states = [s for s, _ in pairs]
    out: list[tuple[DecisionPoint, AnchorInfo | None]] = []
    previous: int | None = None
    for state, point in pairs:
        if point is None:
            continue
        if classification is None or not _completed_main(point):
            out.append((point, None))
            continue
        prior, previous = previous, state.event_source_index
        if prior is None:
            # 1º item principal: não há item anterior e a "âncora" seria o início da partida, com o
            # inventário vazio. Fica no instante da compra (avaliação original).
            out.append((point, None))
            continue
        anchor = commitment_anchor(
            ordered_states, target_index=state.event_source_index, previous_main=prior
        )
        assert anchor is not None  # o próprio estado do alvo é candidato a âncora
        out.append(_at_anchor(point, anchor, participants, match, classification, scope))
    return out


def _at_anchor(
    point: DecisionPoint,
    anchor: DecisionState,
    participants: tuple[ParticipantView, ...],
    match: MatchContext,
    classification: ItemClassificationTable,
    scope: DecisionScope,
) -> tuple[DecisionPoint, AnchorInfo]:
    view = build_pre_decision_view(anchor)
    tolerated = tolerate(view, classification)
    reasons = [
        r for r in point.reasons if r.code in _TARGET_CODES or r.code == REASON_MISSING_CATALOG
    ]
    participant = next(p for p in participants if p.participant_id == point.participant_id)
    ordinal: int | None = None
    if tolerated is None:
        subs = [r.code for r in view.pre_decision_reasons] or [
            "UNSPECIFIED_PRE_DECISION_UNCERTAINTY"
        ]
        reasons.append(EligibilityReason(REASON_INVENTORY_UNCERTAIN, tuple(dict.fromkeys(subs))))
        scope_subs = list(scope.out_of_scope_reasons(match, participant))
    else:
        main_units = 0
        unclassifiable = False
        for item_id, count in tolerated.view.inventory_items:
            item_class = classification.classify(item_id).item_class
            if item_class is ItemClass.MAIN:
                main_units += count
            elif item_class in _UNCLASSIFIABLE:
                unclassifiable = True
        if unclassifiable:
            reasons.append(
                EligibilityReason(REASON_INVENTORY_UNCERTAIN, ("OWNED_ITEM_UNCLASSIFIABLE",))
            )
            scope_subs = list(scope.out_of_scope_reasons(match, participant))
        else:
            ordinal = main_units + 1
            scope_subs = list(scope.out_of_scope_reasons(match, participant, ordinal))
            if ordinal > MAX_MAIN_ORDINAL:
                scope_subs.append("MAIN_ITEM_ORDINAL_BEYOND_SIXTH")
    if scope_subs:
        reasons.append(EligibilityReason(REASON_OUT_OF_SCOPE, tuple(dict.fromkeys(scope_subs))))
    eligible = not reasons
    used = tolerated.view if tolerated is not None else view
    features = (
        extract_features(used, participants=participants, match=match, main_item_ordinal=ordinal)
        if eligible and ordinal is not None
        else None
    )
    info = AnchorInfo(
        anchor.id,
        anchor.event_source_index,
        view.event_timestamp_ms,
        bool(tolerated and tolerated.tolerated),
        tolerated.removed_item_ids if tolerated is not None else (),
    )
    revised = replace(
        point,
        main_item_ordinal=ordinal if ordinal is not None else point.main_item_ordinal,
        eligible=eligible and features is not None,
        reasons=tuple(reasons),
        budget=assess_budget(used),
        features=features,
    )
    return revised, info


__all__ = ["MAX_MAIN_ORDINAL", "AnchorInfo", "anchor_points"]
