from __future__ import annotations

import json
import uuid
from dataclasses import replace
from pathlib import Path

import pytest

from riftlab.candidates.domain import (
    Affordability,
    CandidateContext,
    CandidateInputError,
    ItemRules,
    UserBudget,
    enumerate_candidates,
)
from riftlab.candidates.rules import load_item_rules
from riftlab.catalog.classification import classify_catalog
from riftlab.catalog.domain import CatalogVersion, Item
from riftlab.catalog.infrastructure.curation import curate_data_dragon_items
from riftlab.decisions.features import GoldView, LevelView, PreDecisionView, build_pre_decision_view
from riftlab.shared.api.enums import BudgetSource
from riftlab.timeline.application import RECONSTRUCTION_ALGORITHM_VERSION
from riftlab.timeline.domain import DecisionState

CATALOG_ID = uuid.UUID(int=42)
ROOT = Path(__file__).resolve().parents[4]
if (ROOT / "backend").is_dir() is False:
    ROOT = Path(__file__).resolve().parents[3]
CATALOG = CatalogVersion(CATALOG_ID, "16.17", "16.17.1", "hash", "map", "raw", "data_dragon_v5")
RULES = ItemRules("fixture_v1", "16.17", "data_dragon_v5", frozenset(), (), True, True, True)


def item(item_id: int, price: int, recipe: tuple[int, ...] = (), **flags: object) -> Item:
    return Item(
        CATALOG_ID,
        item_id,
        price,
        [{"itemId": child, "quantity": recipe.count(child)} for child in sorted(set(recipe))],
        {
            "name": f"synthetic {item_id}",
            "tags": [],
            "stats": {},
            "into": [999] if item_id < 100 else [],
        },
        {"purchasable": True, "maps": [11], **flags},
    )


def context(owned: tuple[tuple[int, int], ...] = (), gold: int | None = None) -> CandidateContext:
    view = PreDecisionView(
        uuid.UUID(int=5),
        "DEMO-1",
        1,
        0,
        1000,
        owned,
        3340,
        GoldView(
            gold,
            BudgetSource.FRAME_ESTIMATE.value if gold is not None else BudgetSource.UNKNOWN.value,
            10 if gold is not None else None,
        ),
        LevelView(None, None, None),
        False,
        (),
    )
    return CandidateContext(view, "16.17", 11, 1, "Annie")


def run(
    items: tuple[Item, ...],
    owned: tuple[tuple[int, int], ...] = (),
    *,
    rules: ItemRules = RULES,
    budget: UserBudget | None = None,
    ctx: CandidateContext | None = None,
):
    return enumerate_candidates(
        ctx or context(owned),
        CATALOG,
        items,
        classify_catalog(
            {entry.item_id: entry for entry in items}, curation_rules_version="data_dragon_v5"
        ),
        rules,
        user_budget=budget,
    )


def only_cost(items: tuple[Item, ...], owned: tuple[tuple[int, int], ...]) -> int:
    result = run(items, owned)
    return next(
        candidate.remaining_cost for candidate in result.candidates if candidate.item_id == 100
    )


def test_repeated_components_and_budget_masks() -> None:
    items = (item(1, 200), item(100, 500, (1, 1)))
    assert only_cost(items, ((1, 1),)) == 300
    assert only_cost(items, ((1, 2),)) == 100
    assert only_cost(items, ()) == 500
    result = run(items, ((1, 1),), ctx=context(((1, 1),), 9999))
    assert result.candidates[0].affordability is Affordability.UNKNOWN
    assert "FRAME_GOLD_ESTIMATE_NOT_EXACT" in result.candidates[0].evidence_codes
    assert (
        run(items, budget=UserBudget(0)).candidates[0].affordability
        is Affordability.ABOVE_USER_BUDGET
    )
    assert (
        run(items, budget=UserBudget(500)).candidates[0].affordability
        is Affordability.WITHIN_USER_BUDGET
    )


def test_ancestor_descendant_ready_component_and_competing_branches() -> None:
    items = (item(1, 100), item(2, 150, (1,)), item(3, 250, (1,)), item(100, 500, (2, 3)))
    assert only_cost(items, ((1, 1),)) == 400  # one unit cannot credit both branches
    assert only_cost(items, ((1, 2),)) == 300
    assert only_cost(items, ((2, 1), (1, 1))) == 250
    assert only_cost(items, ((2, 1), (1, 2))) == 250  # loose extras do not add credit


def test_illegal_reasons_and_zero_candidates() -> None:
    items = (
        item(1, 100),
        item(100, 200, (1,)),
        item(101, 200, (1,), maps=[12]),
        item(102, 200, (1,), requiredChampion="Lux"),
        item(103, 200, (1,), purchasable=False),
        item(323003, 200, (1,)),
    )
    result = run(items, ((100, 1),))
    reasons = {entry.item_id: entry.reason_codes for entry in result.exclusions}
    assert "ALREADY_OWNED" in reasons[100]
    assert "MAP_ILLEGAL" in reasons[101]
    assert "REQUIRED_CHAMPION" in reasons[102]
    assert "NOT_PURCHASABLE" in reasons[103]
    assert "RULE_EXCLUDED" in reasons[323003]
    assert result.candidates == ()
    assert result.diagnostic_codes == ("NO_LEGAL_CANDIDATES",)


def test_exclusive_group_slots_and_trinket() -> None:
    items = (item(1, 100), item(100, 200, (1,)), item(101, 200, (1,)))
    grouped = replace(RULES, exclusive_groups=(frozenset({100, 101}),))
    assert (
        "EXCLUSIVE_GROUP_OWNED"
        in {x.item_id: x.reason_codes for x in run(items, ((101, 1),), rules=grouped).exclusions}[
            100
        ]
    )
    assert any(x.item_id == 100 for x in run(items, ((1, 1), (101, 5))).candidates)
    assert (
        "NO_INVENTORY_SLOT"
        in {x.item_id: x.reason_codes for x in run(items, ((101, 6),)).exclusions}[100]
    )
    # trinket is separate: six normal units plus trinket remain legal if one is consumed
    assert run(items, ((1, 1), (101, 5))).candidates


@pytest.mark.parametrize(
    "broken",
    [
        (item(1, 100), item(100, 50, (1,))),
        (item(100, 500, (9,)),),
        (item(100, 500, (100,)),),
        (item(1, 100), item(2, 150, (1,)), item(100, 100, (2,))),
    ],
)
def test_bad_recipe_fails_closed(broken: tuple[Item, ...]) -> None:
    result = run(broken)
    assert "RECIPE_INCOMPATIBLE" in {x.item_id: x.reason_codes for x in result.exclusions}[100]


def test_recipe_expansion_limit_fails_closed() -> None:
    limited = (
        item(1, 1),
        Item(
            CATALOG_ID,
            100,
            1000,
            [{"itemId": 1, "quantity": 300}],
            {"name": "synthetic 100", "tags": [], "stats": {}, "into": []},
            {"purchasable": True, "maps": [11]},
        ),
    )
    assert (
        "RECIPE_INCOMPATIBLE" in {x.item_id: x.reason_codes for x in run(limited).exclusions}[100]
    )


def test_patch_and_rule_mismatch_and_unknown_group() -> None:
    items = (item(1, 100), item(100, 200, (1,)))
    with pytest.raises(CandidateInputError, match="CATALOG_PATCH_OR_RULES_MISMATCH"):
        run(items, rules=replace(RULES, game_patch="16.16"))
    result = run(items, rules=replace(RULES, exclusive_coverage=False, unique_item_ids=False))
    assert result.candidates == ()
    assert "EXCLUSIVE_GROUP_UNKNOWN" in result.exclusions[-1].reason_codes


def test_exact_small_graph_enumeration_never_overcredits() -> None:
    items = (item(1, 100), item(2, 150, (1,)), item(3, 200, (1,)), item(100, 500, (2, 3)))
    for a in range(4):
        for b in range(3):
            for c in range(3):
                owned = tuple((i, n) for i, n in ((1, a), (2, b), (3, c)) if n)
                if sum(n for _, n in owned) > 6:
                    continue
                result = run(items, owned)
                for candidate in result.candidates:
                    assert 0 <= candidate.remaining_cost <= items[-1].price


def test_local_patch_rules_are_pinned_and_conservative() -> None:
    for patch in ("16.16", "16.17"):
        rules = load_item_rules(ROOT / "configs" / "item-rules" / f"{patch}.json")
        assert rules.game_patch == patch
        assert rules.high_id_namespace_excluded
        assert not rules.exclusive_coverage


def test_patch_16_19_rules_cover_groups_and_duplicates_from_curation() -> None:
    rules = load_item_rules(ROOT / "configs" / "item-rules" / "16.19.json")
    groups = set(rules.exclusive_groups)

    assert rules.game_patch == "16.19"
    assert rules.exclusive_coverage and rules.unique_item_ids
    assert rules.high_id_namespace_excluded and rules.excluded_ids == frozenset()
    assert frozenset({3036, 3033}) in groups  # Lord Dominik's x Mortal Reminder
    assert frozenset({3074, 3748}) in groups  # Ravenous x Titanic Hydra
    assert frozenset({3053, 6673}) in groups  # Sterak's x Immortal Shieldbow (Lifeline)
    assert frozenset({3074, 3077}) not in groups  # Tiamat is consumed by its hydra
    assert frozenset({3057, 3078}) not in groups  # Sheen is consumed by Trinity Force
    assert all(len(group) == 2 for group in groups)


def test_patch_16_19_rules_release_candidates_on_the_pinned_catalog() -> None:
    raw_path = (
        ROOT
        / ".private"
        / "validation"
        / "sprint-3"
        / "CC-321"
        / "catalog"
        / "item-16.19.1-en_US.json"
    )
    if not raw_path.exists():
        pytest.skip("local private Data Dragon fixture is intentionally outside Docker context")
    raw = json.loads(raw_path.read_text(encoding="utf-8"))["data"]
    curated, excluded = curate_data_dragon_items(raw)
    catalog = replace(CATALOG, game_patch="16.19", data_dragon_version="16.19.1")
    items = tuple(
        Item(catalog.id, e.item_id, e.price, e.recipe, e.attributes, e.flags, e.schema_version)
        for e in curated
    )
    classification = classify_catalog(
        {entry.item_id: entry for entry in items},
        curation_rules_version="data_dragon_v5",
        excluded_item_ids=frozenset(entry.item_id for entry in excluded),
    )
    rules = load_item_rules(ROOT / "configs" / "item-rules" / "16.19.json")
    result = enumerate_candidates(
        replace(context(), game_patch="16.19"), catalog, items, classification, rules
    )
    reasons = {entry.item_id: entry.reason_codes for entry in result.exclusions}
    assert result.candidates
    assert all("EXCLUSIVE_GROUP_UNKNOWN" not in r for r in reasons.values())
    assert all("DUPLICATE_RULE_UNKNOWN" not in r for r in reasons.values())


@pytest.mark.parametrize("patch", ["16.16", "16.17"])
def test_curated_local_data_dragon_fail_closed_with_real_twin_ids(patch: str) -> None:
    raw_path = (
        ROOT
        / ".private"
        / "validation"
        / "sprint-2"
        / "CC-204"
        / "data-dragon"
        / f"item-{patch}.1.json"
    )
    if not raw_path.exists():
        pytest.skip("local private Data Dragon fixture is intentionally outside Docker context")
    raw = json.loads(raw_path.read_text(encoding="utf-8"))["data"]
    curated, excluded = curate_data_dragon_items(raw)
    catalog = replace(CATALOG, game_patch=patch, data_dragon_version=f"{patch}.1")
    items = tuple(
        Item(
            catalog.id,
            entry.item_id,
            entry.price,
            entry.recipe,
            entry.attributes,
            entry.flags,
            entry.schema_version,
        )
        for entry in curated
    )
    classification = classify_catalog(
        {entry.item_id: entry for entry in items},
        curation_rules_version="data_dragon_v5",
        excluded_item_ids=frozenset(entry.item_id for entry in excluded),
    )
    rules = load_item_rules(ROOT / "configs" / "item-rules" / f"{patch}.json")
    result = enumerate_candidates(
        replace(context(), game_patch=patch), catalog, items, classification, rules
    )
    reasons = {entry.item_id: entry.reason_codes for entry in result.exclusions}
    assert classification.classify(323003).item_class.name == "MAIN"
    assert "RULE_EXCLUDED" in reasons[323003]
    assert all(entry.item_id in reasons for entry in excluded)
    assert result.candidates == ()  # group/duplicate facts absent from this source
    assert "EXCLUSIVE_GROUP_UNKNOWN" in reasons[3031]


def test_future_fields_and_historical_target_cannot_change_candidates() -> None:
    state = DecisionState(
        uuid.UUID(int=5),
        "DEMO-1",
        1,
        0,
        state_version=RECONSTRUCTION_ALGORITHM_VERSION,
        snapshot={
            "eventTimestampMs": 1000,
            "inventory": {"items": {"1": 1}, "trinketItemId": 3340},
            "purchasedItemId": 100,
            "finalItems": [100],
        },
        quality={
            "goldEstimate": {"value": None, "origin": "UNKNOWN", "ageMs": None},
            "levelObservation": {"value": None, "ageMs": None, "absentReason": "NO_PRIOR_FRAME"},
            "preDecisionQuality": {"uncertain": False, "reasons": []},
            "retrospectiveTarget": {"status": "COMPLETED"},
            "outcome": "WIN",
        },
    )
    changed = replace(
        state,
        snapshot={**state.snapshot, "purchasedItemId": 999, "finalItems": [999]},
        quality={
            **state.quality,
            "outcome": "LOSS",
            "retrospectiveTarget": {"status": "REVERTED_BY_UNDO"},
        },
    )
    items = (item(1, 100), item(100, 200, (1,)))
    a = run(items, ctx=replace(context(), view=build_pre_decision_view(state)))
    b = run(items, ctx=replace(context(), view=build_pre_decision_view(changed)))
    assert a == b
