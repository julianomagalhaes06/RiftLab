"""Resumo do compromisso para a revelação (CC-1501). Só leitura no banco.

Usa a compra histórica (classificação no instante em que o item ficou pronto e o início do
caminho), por isso só aparece depois que o jogador revela a resposta no modo Estudo."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from riftlab.candidates.domain import CandidateContext, CandidateInputError, enumerate_candidates
from riftlab.candidates.rules import resolve_catalog_item_rules
from riftlab.catalog.classification import ItemClass
from riftlab.dataset.rows import RowsError, _catalog
from riftlab.decisions.commitment import CompletionKind, assess_completion, path_start
from riftlab.decisions.features import build_pre_decision_view
from riftlab.decisions.models import DecisionPointModel
from riftlab.decisions.tolerance import tolerate
from riftlab.timeline.repository import SqlAlchemyTimelineRepository


async def commitment_summary(
    session: AsyncSession, point: DecisionPointModel, configs: Path
) -> dict[str, Any] | None:
    anchor = point.anchor if isinstance(point.anchor, dict) else None
    if anchor is None or point.catalog_id is None or point.target_class != ItemClass.MAIN.value:
        return None
    try:
        bundle = await _catalog(session, point.catalog_id)
    except RowsError:
        return None
    rules = await resolve_catalog_item_rules(session, configs / "item-rules", bundle)
    timeline = SqlAlchemyTimelineRepository(session)
    states = [
        s
        for s in await timeline.list_decision_states(point.match_id, point.participant_id)
        if s.state_version == point.reconstruction_version
    ]
    target = next((s for s in states if s.id == point.decision_state_id), None)
    if target is None:
        return None
    items = {item.item_id: item for item in bundle.items}
    kind_payload: dict[str, Any] = {"kind": CompletionKind.UNKNOWN.value}
    tolerated = tolerate(build_pre_decision_view(target), bundle.classification)
    if tolerated is not None and rules is not None:
        champion = (point.features or {}).get("values", {}).get("self_champion_id")
        try:
            result = enumerate_candidates(
                CandidateContext(tolerated.view, bundle.catalog.game_patch, 11, int(champion or 0)),
                bundle.catalog,
                bundle.items,
                bundle.classification,
                rules,
            )
            kind_payload = assess_completion(
                point.target_item_id, result.candidates, {i: it.price for i, it in items.items()}
            ).to_payload()
        except (CandidateInputError, TypeError, ValueError):
            pass
    # Item principal anterior = o último estado antes da âncora (a âncora é a primeira compra
    # depois dele); None quando a âncora é a primeira compra da partida.
    anchor_index = int(anchor["eventSourceIndex"])
    before = [s.event_source_index for s in states if s.event_source_index < anchor_index]
    previous = max(before) if before else None
    start = path_start(
        states,
        target_index=point.event_source_index,
        target_item_id=point.target_item_id,
        previous_main=previous,
        items=items,
    )
    start_item = start.snapshot.get("purchasedItemId") if start is not None else None
    return {
        **kind_payload,
        "anchorTimestampMs": anchor.get("timestampMs"),
        "completedAtMs": point.event_timestamp_ms,
        "pathStartItemId": start_item,
        "pathStartItemName": items[start_item].attributes.get("name")
        if isinstance(start_item, int) and start_item in items
        else None,
        "pathStartTimestampMs": start.snapshot.get("eventTimestampMs")
        if start is not None
        else None,
    }


__all__ = ["commitment_summary"]
