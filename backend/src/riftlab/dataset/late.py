"""CC-701 experimental late-item planning. No changes to served decision scope.

Labels use retrospective selection; predictors and legality use the FIRST anchor
following the previous main completion. Invalid anchors never slide forward.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from typing import Any

from riftlab.candidates.domain import (
    CandidateContext,
    CandidateInputError,
    ItemRules,
    enumerate_candidates,
)
from riftlab.catalog.classification import ItemClass, ItemClassificationTable
from riftlab.catalog.domain import CatalogVersion, Item
from riftlab.decisions.domain import MatchContext, ParticipantView
from riftlab.decisions.features import build_pre_decision_view, extract_features
from riftlab.decisions.scope import DecisionScope
from riftlab.decisions.selection import DecisionPoint, evaluate_decision
from riftlab.timeline.domain import DecisionState

LATE_SCHEMA_VERSION = "late_planning_v1"
ORDINAL_REASON = ("OUT_OF_SCOPE", "MAIN_ITEM_ORDINAL_BEYOND_SECOND")


def quality_ready(point: DecisionPoint) -> bool:
    return point.target_class is ItemClass.MAIN and not any(
        (reason.code, sub) != ORDINAL_REASON
        for reason in point.reasons
        for sub in (reason.sub_reasons or ("",))
    )


def planning_rows(
    states: Sequence[DecisionState],
    *,
    match: MatchContext,
    participants: tuple[ParticipantView, ...],
    catalog: CatalogVersion,
    items: tuple[Item, ...],
    classification: ItemClassificationTable,
    scope: DecisionScope,
    rules: ItemRules,
    split: str,
) -> tuple[list[dict[str, Any]], Counter[str]]:
    """One participant's purchase states; excludes illegal labels with diagnostics.

    The anchor is fixed without consulting target identity or a favorable later
    state. Strict timestamp separation rejects same-transaction label shortcuts.
    """
    if split not in ("train", "validation", "test"):
        raise ValueError("split inválido")
    if len({(s.match_id, s.participant_id) for s in states}) > 1:
        raise ValueError("mistura de participantes/partidas")
    ordered = sorted(states, key=lambda s: s.event_source_index)
    if len({s.event_source_index for s in ordered}) != len(ordered):
        raise ValueError("índice de evento duplicado")
    points = [
        evaluate_decision(
            s,
            match=match,
            participants=participants,
            catalog=catalog,
            classification=classification,
            scope=scope,
        )
        for s in ordered
    ]
    own = next(
        (p for p in participants if ordered and p.participant_id == ordered[0].participant_id), None
    )
    rows: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    previous_main: DecisionPoint | None = None
    for pos, (state, point) in enumerate(zip(ordered, points, strict=True)):
        if point is None or point.target_class is not ItemClass.MAIN:
            continue
        previous = previous_main
        previous_main = point
        ordinal = point.main_item_ordinal
        if ordinal not in (3, 4, 5):
            continue
        counts[f"ORDINAL_{ordinal}_OBSERVED"] += 1
        if not quality_ready(point):
            counts["TARGET_QUALITY_REJECTED"] += 1
            continue
        counts[f"ORDINAL_{ordinal}_QUALITY_READY"] += 1
        if (
            previous is None
            or not quality_ready(previous)
            or previous.main_item_ordinal != ordinal - 1
        ):
            counts["PREVIOUS_MAIN_INVALID"] += 1
            continue
        anchor = next(
            (s for s in ordered[:pos] if s.event_source_index > previous.event_source_index), None
        )
        if anchor is None:
            counts["NO_PRIOR_ANCHOR"] += 1
            continue
        view = build_pre_decision_view(anchor)
        if view.event_timestamp_ms >= point.event_timestamp_ms:
            counts["ANCHOR_NOT_STRICTLY_EARLIER"] += 1
            continue
        if view.pre_decision_uncertain or view.pre_decision_reasons:
            counts["ANCHOR_UNCERTAIN"] += 1
            continue
        owned_classes = [
            (classification.classify(i).item_class, n) for i, n in view.inventory_items
        ]
        if any(
            c in (ItemClass.UNKNOWN, ItemClass.EXCLUDED, ItemClass.UNCLASSIFIED)
            for c, _ in owned_classes
        ):
            counts["ANCHOR_UNCLASSIFIABLE"] += 1
            continue
        if sum(n for c, n in owned_classes if c is ItemClass.MAIN) != ordinal - 1:
            counts["ANCHOR_ORDINAL_MISMATCH"] += 1
            continue
        assert own is not None
        try:
            result = enumerate_candidates(
                CandidateContext(view, match.patch, match.map_id, own.champion_id),
                catalog,
                items,
                classification,
                rules,
            )
        except CandidateInputError as exc:
            counts[f"CANDIDATES_{exc.code}"] += 1
            continue
        ids = [c.item_id for c in result.candidates]
        if point.target_item_id not in ids:
            counts["TARGET_NOT_LEGAL_AT_ANCHOR"] += 1
            continue
        vector = extract_features(
            view, participants=participants, match=match, main_item_ordinal=ordinal
        )
        # No economics at all: even anchor gold is outside this task's allowlist.
        values = {k: v for k, v in vector.values.items() if not k.startswith("gold_")}
        masks = {k: v for k, v in vector.masks.items() if k in values}
        owned_main = [
            i
            for i, n in view.inventory_items
            if classification.classify(i).item_class is ItemClass.MAIN
            for _ in range(n)
        ]
        rows.append(
            {
                "schemaVersion": LATE_SCHEMA_VERSION,
                "task": "late_planning",
                "matchId": match.match_id,
                "participantId": state.participant_id,
                "split": split,
                "championId": own.champion_id,
                "mainItemOrdinal": ordinal,
                "targetItemId": point.target_item_id,
                "eventTimestampMs": point.event_timestamp_ms,
                "anchorEventSourceIndex": anchor.event_source_index,
                "targetEventSourceIndex": state.event_source_index,
                "anchorTimestampMs": view.event_timestamp_ms,
                "ownedMainItemIds": sorted(owned_main),
                "candidateItemIds": ids,
                "candidates": [{"itemId": i} for i in ids],
                "features": values,
                "featureMasks": masks,
                "targetLegal": True,
                "subsets": [
                    f"ordinal:{ordinal}",
                    f"champion:{own.champion_id}",
                    f"cell:{own.champion_id}|{ordinal}",
                ],
            }
        )
        counts[f"ORDINAL_{ordinal}_ROWS"] += 1
    return rows, counts
