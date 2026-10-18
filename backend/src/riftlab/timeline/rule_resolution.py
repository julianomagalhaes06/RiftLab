"""Resolução empírica com proteção das dependências de catálogo na herança."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from riftlab.catalog.diff import catalog_diff, patch_catalog
from riftlab.timeline.empirical_rules import GRANTED_ITEM_IDS_BY_RULE, EmpiricalRules


async def resolve_rules(
    session: AsyncSession,
    rules: EmpiricalRules,
    patch: str,
    catalog_id: uuid.UUID | None,
) -> tuple[frozenset[str], list[dict[str, Any]]]:
    resolution = rules.resolve(patch)
    provenance = rules.provenance_for(patch)
    if resolution.application != "INHERITED":
        for entry in provenance:
            if entry["ruleId"] == "R4s" and entry.get("application") != "WITHHELD":
                entry["systemItemIds"] = sorted(rules.system_item_ids)
        return resolution.rule_ids, provenance
    from riftlab.dataset.rows import _catalog

    source = await patch_catalog(session, resolution.source_patch or "")
    target = await _catalog(session, catalog_id) if catalog_id else None
    dependencies = {
        **GRANTED_ITEM_IDS_BY_RULE,
        "R8": frozenset({1001, 1201, 1202}),
        "R8t": frozenset({1001, 1202}),
        "R4s": rules.system_item_ids,
    }
    changed = set()
    if source and target:
        a = {i.item_id: i for i in source.items}
        b = {i.item_id: i for i in target.items}
        changed = set(catalog_diff(a, b, source.classification, target.classification))
        # Um item ausente nos dois snapshots não prova mudança entre patches;
        # ele continua desconhecido e o comportamento abaixo falha fechado.
        unavailable_dependencies = {
            item_id
            for ids in dependencies.values()
            for item_id in ids
            if item_id not in a and item_id not in b
        }
    else:
        unavailable_dependencies = set()
    enabled = set(resolution.rule_ids)
    for entry in provenance:
        ids = dependencies.get(entry["ruleId"], frozenset())
        unavailable = bool(ids & unavailable_dependencies)
        if ids and (source is None or target is None or ids & changed or unavailable):
            enabled.discard(entry["ruleId"])
            entry["application"] = "WITHHELD"
            entry["withheldReason"] = (
                "SOURCE_CATALOG_UNAVAILABLE"
                if source is None or target is None
                else "DEPENDENCY_UNAVAILABLE"
                if unavailable and not ids & changed
                else "CATALOG_CHANGED"
            )
            entry["changedItemIds"] = sorted(ids & changed)
        elif entry["ruleId"] == "R4s":
            # Parâmetro necessário para reproduzir exatamente o reducer depois.
            entry["systemItemIds"] = sorted(rules.system_item_ids)
    return frozenset(enabled), provenance
