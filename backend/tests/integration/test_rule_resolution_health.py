from __future__ import annotations

from dataclasses import replace

import pytest
from pipeline_helpers import unique_patch
from sqlalchemy import delete

from riftlab.catalog.application import sync_catalog
from riftlab.catalog.infrastructure.demo_source import DemoCatalogSource
from riftlab.catalog.models import (
    CatalogExcludedItemModel,
    CatalogIdentityModel,
    CatalogVersionModel,
    ItemModel,
)
from riftlab.shared.db import unit_of_work
from riftlab.timeline.empirical_rules import (
    INHERITANCE_LATEST_EARLIER,
    STATUS_SUPPORTED,
    EmpiricalRules,
    RuleEntry,
)
from riftlab.timeline.rule_resolution import resolve_rules


class _PatchCatalogSource:
    async def load_catalog(self, patch: str):
        document = await DemoCatalogSource().load_catalog("DEMO-1")
        return replace(document, game_patch=patch, data_dragon_version=f"{patch}.1")


@pytest.mark.asyncio
async def test_dependency_missing_from_both_catalogs_fails_closed_without_fake_change(
    session_factory,
):
    source_patch = unique_patch()
    target_patch = f"{source_patch.rsplit('.', 1)[0]}.{int(source_patch.rsplit('.', 1)[1]) + 1}"
    source = _PatchCatalogSource()
    source_catalog = await sync_catalog(session_factory, source, source_patch)
    target_catalog = await sync_catalog(session_factory, source, target_patch)
    rules = EmpiricalRules(
        entries={
            "R7b": RuleEntry(
                rule_id="R7b",
                rule_version=1,
                description="teste de proveniência",
                status=STATUS_SUPPORTED,
                patches_validated=frozenset({source_patch}),
                activated_in_version=11,
            )
        },
        inheritance=INHERITANCE_LATEST_EARLIER,
    )
    try:
        async with unit_of_work(session_factory) as session:
            enabled, provenance = await resolve_rules(
                session, rules, target_patch, target_catalog.id
            )
        assert "R7b" not in enabled
        entry = next(item for item in provenance if item["ruleId"] == "R7b")
        assert entry["application"] == "WITHHELD"
        assert entry["withheldReason"] == "DEPENDENCY_UNAVAILABLE"
        assert entry["changedItemIds"] == []
    finally:
        ids = [source_catalog.id, target_catalog.id]
        async with unit_of_work(session_factory) as session:
            await session.execute(delete(ItemModel).where(ItemModel.catalog_id.in_(ids)))
            await session.execute(
                delete(CatalogExcludedItemModel).where(CatalogExcludedItemModel.catalog_id.in_(ids))
            )
            await session.execute(
                delete(CatalogIdentityModel).where(
                    CatalogIdentityModel.canonical_catalog_id.in_(ids)
                )
            )
            await session.execute(
                delete(CatalogVersionModel).where(CatalogVersionModel.id.in_(ids))
            )
