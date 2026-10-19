"""Versioned item-set contexts consumed by reconstruction and empirical audits.

The catalog content identity and the materialized item set are deliberately
separate: a recipe closure and a complete canonical catalog share a catalog_id
but are different reducer inputs.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from riftlab.catalog.domain import CatalogVersion, ExcludedCatalogItem, Item


class CatalogContextStrategy(StrEnum):
    EVENT_RECIPE_CLOSURE_V11 = "EVENT_RECIPE_CLOSURE_V11"
    FULL_CANONICAL_V1 = "FULL_CANONICAL_V1"


class CatalogItemSource(Protocol):
    async def get_item(self, catalog_id: UUID, item_id: int) -> Item | None: ...

    async def list_items(self, catalog_id: UUID) -> list[Item]: ...

    async def list_excluded_items(self, catalog_id: UUID) -> list[ExcludedCatalogItem]: ...


@dataclass(frozen=True, slots=True)
class CatalogContextPolicy:
    """Versioned active reconstruction selection; prospective modes stay opt-in."""

    policy_version: str
    active_reconstruction_contexts: tuple[tuple[int, CatalogContextStrategy], ...]
    prospective_audit_context: CatalogContextStrategy
    # Versões em que um domínio do registro de regras pode escolher outro contexto
    # (o mesmo em que foi confirmado); fora delas só o contexto ativo vale.
    registry_domain_versions: tuple[int, ...] = ()

    def resolve_for_domain(
        self, reconstruction_version: int, registry_context: str
    ) -> CatalogContextStrategy:
        default = self.resolve_active(reconstruction_version)
        strategy = CatalogContextStrategy(registry_context)
        if strategy is not default and reconstruction_version not in self.registry_domain_versions:
            raise ValueError(
                f"reconstrução v{reconstruction_version} não aceita contexto de domínio {strategy}"
            )
        return strategy

    def resolve_active(self, reconstruction_version: int) -> CatalogContextStrategy:
        matches = [
            strategy
            for version, strategy in self.active_reconstruction_contexts
            if version == reconstruction_version
        ]
        if len(matches) != 1:
            raise ValueError(
                f"sem contexto ativo único para reconstrução v{reconstruction_version}"
            )
        return matches[0]


CATALOG_CONTEXT_POLICY_V1 = CatalogContextPolicy(
    policy_version="CATALOG_CONTEXT_POLICY_V1",
    active_reconstruction_contexts=(
        (10, CatalogContextStrategy.EVENT_RECIPE_CLOSURE_V11),
        (11, CatalogContextStrategy.EVENT_RECIPE_CLOSURE_V11),
        (12, CatalogContextStrategy.EVENT_RECIPE_CLOSURE_V11),
    ),
    prospective_audit_context=CatalogContextStrategy.FULL_CANONICAL_V1,
    # v12 (CC-802): domínio confirmado em FULL_CANONICAL_V1 reconstrói em FULL.
    registry_domain_versions=(12,),
)


@dataclass(frozen=True, slots=True)
class CatalogContextDescriptor:
    strategy: CatalogContextStrategy
    context_version: str
    policy_version: str
    catalog_id: UUID
    game_patch: str
    data_dragon_version: str
    mapping_version: str
    curation_rules_version: str
    full_catalog_sha256: str
    full_catalog_item_count: int | None
    materialized_items_sha256: str
    materialized_item_count: int
    materialized_exclusions_sha256: str
    materialized_exclusion_count: int
    seed_item_count: int
    fingerprint: str

    def to_payload(self) -> dict[str, object]:
        return {
            "strategy": self.strategy.value,
            "contextVersion": self.context_version,
            "policyVersion": self.policy_version,
            "catalogId": str(self.catalog_id),
            "gamePatch": self.game_patch,
            "dataDragonVersion": self.data_dragon_version,
            "mappingVersion": self.mapping_version,
            "curationRulesVersion": self.curation_rules_version,
            "fullCatalogSha256": self.full_catalog_sha256,
            "fullCatalogItemCount": self.full_catalog_item_count,
            "materializedItemsSha256": self.materialized_items_sha256,
            "materializedItemCount": self.materialized_item_count,
            "materializedExclusionsSha256": self.materialized_exclusions_sha256,
            "materializedExclusionCount": self.materialized_exclusion_count,
            "seedItemCount": self.seed_item_count,
            "fingerprint": self.fingerprint,
        }


@dataclass(frozen=True, slots=True)
class MaterializedCatalogContext:
    items: Mapping[int, Item]
    excluded_item_ids: frozenset[int]
    descriptor: CatalogContextDescriptor


def _canonical_hash(value: object) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(raw).hexdigest()


def _item_payload(item: Item) -> dict[str, object]:
    return {
        "itemId": item.item_id,
        "price": item.price,
        "recipe": item.recipe,
        "attributes": item.attributes,
        "flags": item.flags,
        "schemaVersion": item.schema_version,
    }


def referenced_item_ids(events: Sequence[object]) -> set[int]:
    """Seed rule shared with timeline.application's frozen v11 event gate."""
    relevant = {
        "ITEM_PURCHASED",
        "ITEM_SOLD",
        "ITEM_DESTROYED",
        "ITEM_UNDO",
    }
    ids: set[int] = set()
    for event in events:
        event_type = getattr(event, "type", None)
        if event_type not in relevant:
            continue
        payload = getattr(event, "payload", None)
        if not isinstance(payload, Mapping):
            continue
        for name in ("itemId", "beforeId", "afterId"):
            value = payload.get(name)
            if isinstance(value, int) and not isinstance(value, bool) and value != 0:
                ids.add(value)
    return ids


async def load_catalog_items_for_context(
    source: CatalogItemSource,
    catalog_id: UUID,
    strategy: CatalogContextStrategy,
    seed_item_ids: set[int],
) -> dict[int, Item]:
    if not isinstance(strategy, CatalogContextStrategy):
        raise ValueError(f"estratégia de catálogo desconhecida: {strategy!r}")
    if strategy is CatalogContextStrategy.FULL_CANONICAL_V1:
        rows = await source.list_items(catalog_id)
        items = {item.item_id: item for item in rows}
        if len(items) != len(rows):
            raise ValueError("catálogo canônico contém item_id duplicado")
        if any(item.catalog_id != catalog_id for item in rows):
            raise ValueError("catálogo canônico retornou item de outra identidade")
        return items

    resolved: dict[int, Item] = {}
    pending = set(seed_item_ids)
    while pending:
        item_id = pending.pop()
        if item_id in resolved:
            continue
        item = await source.get_item(catalog_id, item_id)
        if item is None:
            continue
        if item.catalog_id != catalog_id or item.item_id != item_id:
            raise ValueError("repositório retornou item de identidade divergente")
        resolved[item_id] = item
        for component in item.recipe:
            component_id = component.get("itemId")
            if (
                isinstance(component_id, int)
                and not isinstance(component_id, bool)
                and component_id not in resolved
            ):
                pending.add(component_id)
    return resolved


async def materialize_catalog_context(
    source: CatalogItemSource,
    catalog: CatalogVersion,
    excluded_item_ids: frozenset[int] | None = None,
    *,
    strategy: CatalogContextStrategy,
    seed_item_ids: set[int] | None = None,
    policy_version: str = CATALOG_CONTEXT_POLICY_V1.policy_version,
) -> MaterializedCatalogContext:
    """Materialize a versioned context; FULL_CANONICAL ignores event seeds."""
    if not isinstance(strategy, CatalogContextStrategy):
        raise ValueError(f"estratégia de catálogo desconhecida: {strategy!r}")
    if not isinstance(catalog, CatalogVersion):
        raise ValueError("identidade do catálogo ausente ou inválida")
    seeds = set(seed_item_ids or ())
    items = await load_catalog_items_for_context(source, catalog.id, strategy, seeds)
    if excluded_item_ids is None:
        exclusions = frozenset(row.item_id for row in await source.list_excluded_items(catalog.id))
    else:
        exclusions = frozenset(excluded_item_ids)
    if strategy is CatalogContextStrategy.FULL_CANONICAL_V1:
        full_items = items
        effective_seed_count = 0
        context_version = "1"
    else:
        # The legacy path intentionally does not query the full catalog.
        # Its complete identity hash is known, while its item count is not.
        full_items = items
        effective_seed_count = len(seeds)
        context_version = "11"
    item_hash = _canonical_hash([_item_payload(items[key]) for key in sorted(items)])
    exclusion_hash = _canonical_hash(sorted(exclusions))
    values: dict[str, object] = {
        "strategy": strategy.value,
        "contextVersion": context_version,
        "policyVersion": policy_version,
        "catalogId": str(catalog.id),
        "gamePatch": catalog.game_patch,
        "dataDragonVersion": catalog.data_dragon_version,
        "mappingVersion": catalog.mapping_version,
        "curationRulesVersion": catalog.curation_rules_version,
        "fullCatalogSha256": catalog.sha256,
        "fullCatalogItemCount": (
            len(full_items) if strategy is CatalogContextStrategy.FULL_CANONICAL_V1 else None
        ),
        "materializedItemsSha256": item_hash,
        "materializedItemCount": len(items),
        "materializedExclusionsSha256": exclusion_hash,
        "materializedExclusionCount": len(exclusions),
        "seedItemCount": effective_seed_count,
    }
    fingerprint = _canonical_hash(values)
    descriptor = CatalogContextDescriptor(
        strategy=strategy,
        context_version=context_version,
        policy_version=policy_version,
        catalog_id=catalog.id,
        game_patch=catalog.game_patch,
        data_dragon_version=catalog.data_dragon_version,
        mapping_version=catalog.mapping_version,
        curation_rules_version=catalog.curation_rules_version,
        full_catalog_sha256=catalog.sha256,
        full_catalog_item_count=(
            len(full_items) if strategy is CatalogContextStrategy.FULL_CANONICAL_V1 else None
        ),
        materialized_items_sha256=item_hash,
        materialized_item_count=len(items),
        materialized_exclusions_sha256=exclusion_hash,
        materialized_exclusion_count=len(exclusions),
        seed_item_count=effective_seed_count,
        fingerprint=fingerprint,
    )
    return MaterializedCatalogContext(items, exclusions, descriptor)


def carry_forward_context_version_is_known(
    source_reconstruction_version: int,
    target_strategy: CatalogContextStrategy,
) -> bool:
    """Version gate only. Service must still validate catalog, params and provenance."""
    if target_strategy is not CatalogContextStrategy.EVENT_RECIPE_CLOSURE_V11:
        return False
    return source_reconstruction_version in {10, 11}
