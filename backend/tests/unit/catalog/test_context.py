from __future__ import annotations

from uuid import uuid4

import pytest

from riftlab.catalog.classification import classify_catalog
from riftlab.catalog.context import (
    CATALOG_CONTEXT_POLICY_V1,
    CatalogContextPolicy,
    CatalogContextStrategy,
    carry_forward_context_version_is_known,
    load_catalog_items_for_context,
    materialize_catalog_context,
    referenced_item_ids,
)
from riftlab.catalog.domain import CatalogVersion, ExcludedCatalogItem, Item
from riftlab.decisions.domain import MatchContext, ParticipantView
from riftlab.decisions.features import build_pre_decision_view, extract_features
from riftlab.decisions.scope import DecisionScope
from riftlab.decisions.selection import evaluate_decision
from riftlab.shared.domain import SourceOrigin
from riftlab.timeline.application import (
    _decision_state_from_purchase,
    reconstruct_match,
    reconstruct_participant_purchases,
)
from riftlab.timeline.domain import TimelineEvent, TimelineReplayError
from riftlab.timeline.empirical_rules import RULE_R4S, RULE_R8
from support.reducer_scenarios import FRAMES, P1, buy, destroy, ev


class MemoryCatalog:
    def __init__(self, catalog_id, items):
        self.catalog_id = catalog_id
        self.items = {item.item_id: item for item in items}
        self.exclusions = [ExcludedCatalogItem(9999, "unsupported", "fixture")]

    async def get_item(self, catalog_id, item_id):
        assert catalog_id == self.catalog_id
        return self.items.get(item_id)

    async def list_items(self, catalog_id):
        assert catalog_id == self.catalog_id
        return list(self.items.values())

    async def list_excluded_items(self, catalog_id):
        assert catalog_id == self.catalog_id
        return self.exclusions


def _catalog_fixture():
    catalog_id = uuid4()
    catalog = CatalogVersion(
        catalog_id, "16.20", "16.20.1", "a" * 64, "map-v1", "b" * 64, "curation-v1"
    )
    items = [
        Item(catalog_id, 1001, 300, attributes={"tags": ["Boots"]}),
        Item(catalog_id, 1201, 0),
        Item(catalog_id, 3020, 1100, [{"itemId": 1001, "quantity": 1}], {"tags": ["Boots"]}),
        Item(catalog_id, 3175, 2100, [{"itemId": 3020, "quantity": 1}]),
        Item(catalog_id, 1038, 350),
    ]
    return catalog, MemoryCatalog(catalog_id, items)


def _events(*, later_successor_purchase: bool) -> list[TimelineEvent]:
    events = [
        buy(0, 1_000, 3020),
        destroy(1, 600_000, 3020),
        destroy(2, 600_000, 1201),
        buy(3, 700_000, 1038),
    ]
    if later_successor_purchase:
        events.append(buy(4, 800_000, 3175))
    return events


def _target_state(events, context):
    purchases = reconstruct_participant_purchases(
        participant_id=P1,
        events=events,
        origin=SourceOrigin.COMPLETED_MATCH,
        frames=FRAMES,
        catalog_id=next(iter(context.items.values())).catalog_id,
        catalog_items=context.items,
        excluded_item_ids=context.excluded_item_ids,
        enabled_rules=frozenset({RULE_R8, RULE_R4S}),
        system_item_ids=frozenset({1201}),
    )
    return next(row for row in purchases if row.purchased_item_id == 1038)


def _target_decision_state(events, context):
    purchase = _target_state(events, context)
    return _decision_state_from_purchase(
        "M",
        next(iter(context.items.values())).catalog_id,
        SourceOrigin.COMPLETED_MATCH,
        purchase,
    )


def _selected_feature_payload(state, context):
    """Exercise the same pure feature path called by selection.py:283."""
    catalog_id = next(iter(context.items.values())).catalog_id
    items = {
        item_id: Item(
            catalog_id,
            item_id,
            item.price,
            ([{"itemId": 1001, "quantity": 1}] if item_id == 1038 else item.recipe),
            {**item.attributes, "into": item.attributes.get("into", [])},
            {"completed": item_id == 1038},
        )
        for item_id, item in context.items.items()
    }
    table = classify_catalog(items, curation_rules_version="curated_demo_v1")
    participants = (
        ParticipantView(1, 100, 100, "BOTTOM"),
        ParticipantView(6, 200, 200, "TOP"),
    )
    match = MatchContext("M", 420, 11, "16.20")
    scope = DecisionScope(
        "synthetic_context_test",
        SourceOrigin.COMPLETED_MATCH,
        frozenset({420}),
        11,
        frozenset({"16.20"}),
        "BOTTOM",
        frozenset({100}),
    )
    point = evaluate_decision(
        state,
        participants=participants,
        match=match,
        catalog=CatalogVersion(
            catalog_id,
            "16.20",
            "16.20.1",
            "a" * 64,
            "map-v1",
            "b" * 64,
            "curated_demo_v1",
        ),
        classification=table,
        scope=scope,
    )
    assert point is not None and point.eligible and point.features is not None
    direct = extract_features(
        build_pre_decision_view(state),
        participants=participants,
        match=match,
        main_item_ordinal=point.main_item_ordinal,
    )
    assert direct == point.features
    return direct.to_payload()


@pytest.mark.asyncio
async def test_legacy_closure_and_full_context_are_distinct_and_fingerprinted():
    catalog, source = _catalog_fixture()
    events = _events(later_successor_purchase=False)
    seeds = referenced_item_ids(events)
    legacy = await materialize_catalog_context(
        source,
        catalog,
        strategy=CatalogContextStrategy.EVENT_RECIPE_CLOSURE_V11,
        seed_item_ids=seeds,
    )
    full = await materialize_catalog_context(
        source,
        catalog,
        strategy=CatalogContextStrategy.FULL_CANONICAL_V1,
        seed_item_ids=seeds,
    )

    assert set(legacy.items) == {1001, 1038, 1201, 3020}
    assert set(full.items) == {1001, 1038, 1201, 3020, 3175}
    assert 3175 not in legacy.items and 3175 in full.items
    assert legacy.excluded_item_ids == full.excluded_item_ids == frozenset({9999})
    assert legacy.descriptor.full_catalog_sha256 == full.descriptor.full_catalog_sha256
    assert legacy.descriptor.materialized_items_sha256 != full.descriptor.materialized_items_sha256
    assert legacy.descriptor.fingerprint != full.descriptor.fingerprint
    assert full.descriptor.seed_item_count == 0
    assert full.descriptor.full_catalog_item_count == 5
    assert legacy.descriptor.full_catalog_item_count is None


@pytest.mark.asyncio
async def test_future_successor_purchase_changes_legacy_closure_but_not_full_prefix():
    catalog, source = _catalog_fixture()
    before = _events(later_successor_purchase=False)
    after = _events(later_successor_purchase=True)
    legacy_before = await materialize_catalog_context(
        source,
        catalog,
        strategy=CatalogContextStrategy.EVENT_RECIPE_CLOSURE_V11,
        seed_item_ids=referenced_item_ids(before),
    )
    legacy_after = await materialize_catalog_context(
        source,
        catalog,
        strategy=CatalogContextStrategy.EVENT_RECIPE_CLOSURE_V11,
        seed_item_ids=referenced_item_ids(after),
    )
    full_before = await materialize_catalog_context(
        source,
        catalog,
        strategy=CatalogContextStrategy.FULL_CANONICAL_V1,
        seed_item_ids=referenced_item_ids(before),
    )
    full_after = await materialize_catalog_context(
        source,
        catalog,
        strategy=CatalogContextStrategy.FULL_CANONICAL_V1,
        seed_item_ids=referenced_item_ids(after),
    )

    assert (
        legacy_before.descriptor.materialized_items_sha256
        != legacy_after.descriptor.materialized_items_sha256
    )
    assert legacy_before.descriptor.fingerprint != legacy_after.descriptor.fingerprint
    assert full_before.descriptor == full_after.descriptor
    legacy_old = _target_state(before, legacy_before)
    legacy_new = _target_state(after, legacy_after)
    full_old = _target_state(before, full_before)
    full_new = _target_state(after, full_after)
    assert legacy_old.pre_decision_uncertain is True
    assert legacy_new.pre_decision_uncertain is False
    assert legacy_old.inventory_before != legacy_new.inventory_before
    assert full_old.pre_decision_uncertain == full_new.pre_decision_uncertain
    assert full_old.inventory_before == full_new.inventory_before
    assert full_old.pre_decision_reasons == full_new.pre_decision_reasons


@pytest.mark.asyncio
async def test_full_prefix_is_invariant_to_future_target_and_other_participant_events():
    catalog, source = _catalog_fixture()
    prefix = _events(later_successor_purchase=False)
    future_target = _events(later_successor_purchase=True)
    future_other_participant = [
        *prefix,
        ev(5, 900_000, "ITEM_PURCHASED", participant=6, itemId=3175),
    ]
    sequences = (prefix, future_target, future_other_participant)

    contexts = [
        await materialize_catalog_context(
            source,
            catalog,
            strategy=CatalogContextStrategy.FULL_CANONICAL_V1,
            seed_item_ids=referenced_item_ids(events),
        )
        for events in sequences
    ]
    assert contexts[0].descriptor == contexts[1].descriptor == contexts[2].descriptor
    states = [
        _target_decision_state(events, context)
        for events, context in zip(sequences, contexts, strict=True)
    ]
    before_cut = [(state.snapshot, state.quality) for state in states]
    assert before_cut[0] == before_cut[1] == before_cut[2]
    feature_payloads = [
        _selected_feature_payload(state, context)
        for state, context in zip(states, contexts, strict=True)
    ]
    assert feature_payloads[0] == feature_payloads[1] == feature_payloads[2]
    for payload in feature_payloads:
        assert payload["values"]
        assert payload["values"]["game_time_ms"] == 700_000
        assert payload["values"]["self_champion_id"] == 100
        assert payload["values"]["main_item_ordinal"] == 1

    legacy_contexts = [
        await materialize_catalog_context(
            source,
            catalog,
            strategy=CatalogContextStrategy.EVENT_RECIPE_CLOSURE_V11,
            seed_item_ids=referenced_item_ids(events),
        )
        for events in sequences
    ]
    assert len({row.descriptor.fingerprint for row in legacy_contexts}) == 2


def test_versioned_policy_and_carry_forward_are_strict_for_known_contexts():
    closure = CatalogContextStrategy.EVENT_RECIPE_CLOSURE_V11
    full = CatalogContextStrategy.FULL_CANONICAL_V1
    assert CATALOG_CONTEXT_POLICY_V1.resolve_active(11) is closure
    assert CATALOG_CONTEXT_POLICY_V1.prospective_audit_context is full
    assert carry_forward_context_version_is_known(10, closure)
    assert carry_forward_context_version_is_known(11, closure)
    assert not carry_forward_context_version_is_known(9, closure)
    assert not carry_forward_context_version_is_known(10, full)


@pytest.mark.asyncio
async def test_unknown_runtime_strategy_fails_before_catalog_io():
    catalog, source = _catalog_fixture()
    calls = []
    original_get, original_list, original_exclusions = (
        source.get_item,
        source.list_items,
        source.list_excluded_items,
    )

    async def get_item(*args):
        calls.append("get")
        return await original_get(*args)

    async def list_items(*args):
        calls.append("list")
        return await original_list(*args)

    async def list_excluded_items(*args):
        calls.append("excluded")
        return await original_exclusions(*args)

    source.get_item = get_item
    source.list_items = list_items
    source.list_excluded_items = list_excluded_items
    with pytest.raises(ValueError, match="estratégia de catálogo desconhecida"):
        await load_catalog_items_for_context(source, catalog.id, "FUTURE_GUESS", {3020})
    with pytest.raises(ValueError, match="estratégia de catálogo desconhecida"):
        await materialize_catalog_context(
            source, catalog, strategy="FUTURE_GUESS", seed_item_ids={3020}
        )
    assert calls == []


@pytest.mark.asyncio
async def test_active_service_rejects_full_as_default_mode_before_io():
    prospective_override = CatalogContextPolicy(
        policy_version="test-attempt-to-activate-full",
        active_reconstruction_contexts=((12, CatalogContextStrategy.FULL_CANONICAL_V1),),
        prospective_audit_context=CatalogContextStrategy.FULL_CANONICAL_V1,
    )
    with pytest.raises(TimelineReplayError, match="só por domínio confirmado"):
        await reconstruct_match(
            None, None, None, "fixture-no-io", catalog_context_policy=prospective_override
        )


def test_domain_context_needs_a_version_that_allows_registry_domains():
    closed = CatalogContextPolicy(
        policy_version="test-closed",
        active_reconstruction_contexts=((12, CatalogContextStrategy.EVENT_RECIPE_CLOSURE_V11),),
        prospective_audit_context=CatalogContextStrategy.FULL_CANONICAL_V1,
    )
    with pytest.raises(ValueError, match="não aceita contexto de domínio"):
        closed.resolve_for_domain(12, "FULL_CANONICAL_V1")
    assert closed.resolve_for_domain(12, "EVENT_RECIPE_CLOSURE_V11") is (
        CatalogContextStrategy.EVENT_RECIPE_CLOSURE_V11
    )
    assert CATALOG_CONTEXT_POLICY_V1.resolve_for_domain(12, "FULL_CANONICAL_V1") is (
        CatalogContextStrategy.FULL_CANONICAL_V1
    )
    with pytest.raises(ValueError):
        CATALOG_CONTEXT_POLICY_V1.resolve_for_domain(12, "GUESS")


def test_seed_extraction_ignores_non_item_events_zero_and_boolean_ids():
    events = [
        TimelineEvent("M", 0, 1, "ITEM_PURCHASED", {"itemId": 12}),
        TimelineEvent("M", 1, 2, "ITEM_UNDO", {"beforeId": 13, "afterId": 14}),
        TimelineEvent("M", 2, 3, "OTHER", {"itemId": 15}),
        TimelineEvent("M", 3, 4, "ITEM_SOLD", {"itemId": 0}),
        TimelineEvent("M", 4, 5, "ITEM_DESTROYED", {"itemId": True}),
    ]
    assert referenced_item_ids(events) == {12, 13, 14}
