"""CC-302: query/candidate rows for the eligible decision points of a frozen corpus.

Read-only over a prepared database: each eligible point keeps the catalog its
extraction used, and candidates are enumerated with the patch's reviewed item
rules. The historical purchase is the target; whether it is among the legal
candidates is recorded, never forced. Rows carry match IDs and participant
slots only.
"""

from __future__ import annotations

import uuid
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from riftlab.candidates.domain import (
    CandidateContext,
    CandidateInputError,
    ItemRules,
    enumerate_candidates,
)
from riftlab.catalog.classification import ItemClassificationTable, classify_catalog
from riftlab.catalog.domain import CatalogVersion, Item
from riftlab.catalog.repository import SqlAlchemyCatalogRepository
from riftlab.decisions.features import build_pre_decision_view
from riftlab.decisions.models import DecisionPointModel
from riftlab.timeline.application import RECONSTRUCTION_ALGORITHM_VERSION
from riftlab.timeline.repository import SqlAlchemyTimelineRepository

TARGET_MAP = 11


class RowsError(RuntimeError):
    """Database or manifest inconsistent with the frozen corpus."""


@dataclass(frozen=True, slots=True)
class _CatalogBundle:
    catalog: CatalogVersion
    items: tuple[Item, ...]
    classification: ItemClassificationTable


async def _catalog(session: AsyncSession, catalog_id: uuid.UUID) -> _CatalogBundle:
    repository = SqlAlchemyCatalogRepository(session)
    catalog = await repository.get_catalog_version(catalog_id)
    if catalog is None:
        raise RowsError("catálogo do ponto de decisão ausente")
    items = tuple(await repository.list_items(catalog_id))
    excluded = await repository.list_excluded_items(catalog_id)
    classification = classify_catalog(
        {item.item_id: item for item in items},
        curation_rules_version=catalog.curation_rules_version,
        excluded_item_ids=frozenset(entry.item_id for entry in excluded),
    )
    return _CatalogBundle(catalog, items, classification)


def split_assignment(manifest: Mapping[str, Any]) -> dict[str, str]:
    split = manifest.get("split")
    if not isinstance(split, dict):
        raise RowsError("manifesto sem divisão")
    parts = {
        name: split.get(f"{name}MatchIds", [] if name == "validation" else None)
        for name in ("train", "validation", "test")
    }
    if not all(isinstance(ids, list) for ids in parts.values()):
        raise RowsError("divisão do manifesto inválida")
    assignment: dict[str, str] = {}
    for name, ids in parts.items():
        for match_id in cast(list[str], ids):
            if match_id in assignment:
                raise RowsError("divisão do manifesto inválida")
            assignment[match_id] = name
    return assignment


def planning_anchor(
    state_indexes: list[int],
    *,
    ordinal: int,
    first_main_index: int | None,
    target_index: int,
) -> int | None:
    """Source index of the anchor state (CC-407), or None.

    1st main item: the first purchase state of the game. 2nd: the first purchase state
    after the 1st main item. The anchor never lies after the target purchase.
    """
    after = -1 if ordinal == 1 else first_main_index
    if after is None:
        return None
    later = sorted(i for i in state_indexes if after < i <= target_index)
    return later[0] if later else None


async def build_rows(
    session_factory: async_sessionmaker[AsyncSession],
    manifest: Mapping[str, Any],
    rules: ItemRules,
    *,
    task: str = "purchase",
) -> tuple[list[dict[str, Any]], Counter[str]]:
    """Return one row per eligible point of the corpus, plus aggregate counts.

    `purchase` (CC-302): candidates at the instant of the purchase. `planning` (CC-407):
    candidates at an earlier anchor — before the first purchase of the game for the 1st
    main item, or right after the 1st main item for the 2nd — so the purchase-time gold
    cannot reveal the target.
    """
    if task not in ("purchase", "planning"):
        raise RowsError("tarefa desconhecida")
    split_of = split_assignment(manifest)
    if rules.game_patch != manifest.get("patch"):
        raise RowsError("regras de itens de outro patch")
    counts: Counter[str] = Counter()
    rows: list[dict[str, Any]] = []
    catalogs: dict[uuid.UUID, _CatalogBundle] = {}
    async with session_factory() as session:
        result = await session.execute(
            select(DecisionPointModel)
            .where(
                DecisionPointModel.eligible.is_(True),
                DecisionPointModel.reconstruction_version == RECONSTRUCTION_ALGORITHM_VERSION,
            )
            .order_by(
                DecisionPointModel.match_id,
                DecisionPointModel.participant_id,
                DecisionPointModel.event_timestamp_ms,
                DecisionPointModel.event_source_index,
            )
        )
        points = list(result.scalars())
        if len({point.decision_state_id for point in points}) != len(points):
            raise RowsError("estado com mais de um ponto elegível; extrações duplicadas")
        states = SqlAlchemyTimelineRepository(session)
        first_main: dict[tuple[str, int], int] = {}
        if task == "planning":
            ordinal_rows = await session.execute(
                select(
                    DecisionPointModel.match_id,
                    DecisionPointModel.participant_id,
                    DecisionPointModel.event_source_index,
                ).where(
                    DecisionPointModel.main_item_ordinal == 1,
                    DecisionPointModel.reconstruction_version == RECONSTRUCTION_ALGORITHM_VERSION,
                )
            )
            for match_id, participant_id, source_index in ordinal_rows:
                first_main[(match_id, participant_id)] = source_index
        for point in points:
            split = split_of.get(point.match_id)
            if split is None:
                counts["POINT_OUTSIDE_CORPUS"] += 1
                continue
            if point.catalog_id is None:
                raise RowsError("ponto elegível sem catálogo")
            if point.catalog_id not in catalogs:
                catalogs[point.catalog_id] = await _catalog(session, point.catalog_id)
            bundle = catalogs[point.catalog_id]
            state = await states.get_decision_state(point.decision_state_id)
            values = (point.features or {}).get("values", {})
            champion_id = values.get("self_champion_id")
            if state is None or not isinstance(champion_id, int):
                raise RowsError("ponto elegível sem estado ou campeão")
            if task == "planning":
                if point.main_item_ordinal not in (1, 2):
                    counts["PLANNING_ORDINAL_OUT_OF_SCOPE"] += 1
                    continue
                history = [
                    s
                    for s in await states.list_decision_states(point.match_id, point.participant_id)
                    if s.state_version == RECONSTRUCTION_ALGORITHM_VERSION
                ]
                anchor = planning_anchor(
                    [s.event_source_index for s in history],
                    ordinal=point.main_item_ordinal,
                    first_main_index=first_main.get((point.match_id, point.participant_id)),
                    target_index=point.event_source_index,
                )
                if anchor is None:
                    counts["PLANNING_NO_ANCHOR"] += 1
                    continue
                state = next(s for s in history if s.event_source_index == anchor)
                values = {
                    **values,
                    "game_time_ms": state.snapshot.get("eventTimestampMs"),
                    "gold_estimate": None,
                    "gold_stale": None,
                    "level": (state.quality.get("levelObservation") or {}).get("value"),
                    "inventory_items": [
                        [int(k), v]
                        for k, v in (state.snapshot.get("inventory") or {}).get("items", {}).items()
                    ],
                }
            row: dict[str, Any] = {
                "matchId": point.match_id,
                "split": split,
                "participantId": point.participant_id,
                "eventTimestampMs": point.event_timestamp_ms,
                "mainItemOrdinal": point.main_item_ordinal,
                "championId": champion_id,
                "targetItemId": point.target_item_id,
                "task": task,
                "anchorEventSourceIndex": state.event_source_index,
            }
            try:
                candidates = enumerate_candidates(
                    CandidateContext(
                        view=build_pre_decision_view(state),
                        game_patch=bundle.catalog.game_patch,
                        map_id=TARGET_MAP,
                        champion_id=champion_id,
                    ),
                    bundle.catalog,
                    bundle.items,
                    bundle.classification,
                    rules,
                )
            except CandidateInputError as exc:
                row.update(status=exc.code, candidateItemIds=[], targetLegal=False)
                counts[f"INPUT_{exc.code}"] += 1
                rows.append(row)
                continue
            ids = [candidate.item_id for candidate in candidates.candidates]
            reasons = {entry.item_id: entry.reason_codes for entry in candidates.exclusions}
            legal = point.target_item_id in ids
            row.update(
                status="OK" if ids else "ZERO_CANDIDATES",
                candidateItemIds=ids,
                candidates=[
                    {
                        "itemId": candidate.item_id,
                        "remainingCost": candidate.remaining_cost,
                        "ownedComponentCompatible": "OWNED_COMPONENT_COMPATIBLE"
                        in candidate.evidence_codes,
                    }
                    for candidate in candidates.candidates
                ],
                features=dict(values),
                featureMasks=dict((point.features or {}).get("masks", {})),
                targetLegal=legal,
                targetExclusionReasons=[] if legal else list(reasons.get(point.target_item_id, ())),
            )
            counts["ROWS"] += 1
            counts["TARGET_LEGAL" if legal else "TARGET_NOT_IN_CANDIDATES"] += 1
            if not ids:
                counts["ZERO_CANDIDATES"] += 1
            rows.append(row)
    return rows, counts
