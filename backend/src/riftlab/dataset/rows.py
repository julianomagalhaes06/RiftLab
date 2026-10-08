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


async def build_rows(
    session_factory: async_sessionmaker[AsyncSession],
    manifest: Mapping[str, Any],
    rules: ItemRules,
) -> tuple[list[dict[str, Any]], Counter[str]]:
    """Return one row per eligible point of the corpus, plus aggregate counts."""
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
            row: dict[str, Any] = {
                "matchId": point.match_id,
                "split": split,
                "participantId": point.participant_id,
                "eventTimestampMs": point.event_timestamp_ms,
                "mainItemOrdinal": point.main_item_ordinal,
                "championId": champion_id,
                "targetItemId": point.target_item_id,
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
                targetLegal=legal,
                targetExclusionReasons=[] if legal else list(reasons.get(point.target_item_id, ())),
            )
            counts["ROWS"] += 1
            counts["TARGET_LEGAL" if legal else "TARGET_NOT_IN_CANDIDATES"] += 1
            if not ids:
                counts["ZERO_CANDIDATES"] += 1
            rows.append(row)
    return rows, counts
