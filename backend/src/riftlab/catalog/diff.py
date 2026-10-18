"""Diferenças estruturais entre catálogos; preço não define legalidade."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from riftlab.catalog.classification import ItemClassificationTable
from riftlab.catalog.domain import Item
from riftlab.catalog.models import CatalogIdentityModel, CatalogVersionModel


def _semantic(value: Any) -> str:
    """Representa listas de flags/receita como conjuntos ordenados; a ordem não muda regra."""
    if isinstance(value, dict):
        value = {key: _semantic_value(item) for key, item in sorted(value.items())}
    elif isinstance(value, list):
        value = sorted((_semantic_value(item) for item in value), key=str)
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _semantic_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _semantic_value(item) for key, item in sorted(value.items())}
    if isinstance(value, list):
        return sorted((_semantic_value(item) for item in value), key=str)
    return value


def catalog_diff(
    source: Mapping[int, Item],
    target: Mapping[int, Item],
    source_classes: ItemClassificationTable,
    target_classes: ItemClassificationTable,
) -> dict[int, tuple[str, ...]]:
    changes: dict[int, tuple[str, ...]] = {}
    for item_id in sorted(set(source) | set(target)):
        a, b = source.get(item_id), target.get(item_id)
        if a is None or b is None:
            changes[item_id] = ("ADDED" if a is None else "REMOVED",)
            continue
        reasons = []
        if _semantic(a.recipe) != _semantic(b.recipe):
            reasons.append("RECIPE")
        for key in ("purchasable", "maps", "requiredChampion"):
            if _semantic(a.flags.get(key)) != _semantic(b.flags.get(key)):
                reasons.append(key)
        if (
            source_classes.classify(item_id).item_class
            != target_classes.classify(item_id).item_class
        ):
            reasons.append("CLASSIFICATION")
        if reasons:
            changes[item_id] = tuple(reasons)
    return changes


async def patch_catalog(session: AsyncSession, patch: str) -> Any:
    # Import local para evitar ciclo com dataset.rows -> timeline.application.
    from riftlab.dataset.rows import _catalog

    row = await session.scalar(
        select(CatalogVersionModel)
        .join(
            CatalogIdentityModel,
            CatalogIdentityModel.canonical_catalog_id == CatalogVersionModel.id,
        )
        .where(CatalogVersionModel.game_patch == patch)
        .order_by(CatalogVersionModel.created_at.desc(), CatalogVersionModel.id.desc())
        .limit(1)
    )
    return await _catalog(session, row.id) if row else None
