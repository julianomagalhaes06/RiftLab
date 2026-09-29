"""Versioned, pre-decision candidate legality and multiset completion cost.

The caller must load the exact catalog row and its complete item set. No
repository, current-catalog resolution, retrospective target or score enters
this module. Rules are explicit input, tied to patch and curation version.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, cast

from riftlab.catalog.classification import (
    ITEM_CLASSIFICATION_VERSION,
    ItemClass,
    ItemClassificationTable,
)
from riftlab.catalog.domain import CatalogVersion, Item
from riftlab.decisions.features import PreDecisionView
from riftlab.shared.api.enums import BudgetSource

CANDIDATE_ALGORITHM_VERSION = "candidate_legality_v1"
MAX_RECIPE_EXPANSIONS = 256


class CandidateInputError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class Affordability(StrEnum):
    UNKNOWN = "UNKNOWN"
    WITHIN_USER_BUDGET = "WITHIN_USER_BUDGET"
    ABOVE_USER_BUDGET = "ABOVE_USER_BUDGET"


@dataclass(frozen=True, slots=True)
class ItemRules:
    version: str
    game_patch: str
    curation_rules_version: str
    # Explicit exclusions and groups have sources in the versioned manifest.
    excluded_ids: frozenset[int]
    exclusive_groups: tuple[frozenset[int], ...]
    # If false, the catalog cannot prove an item has no other exclusive group.
    exclusive_coverage: bool
    unique_item_ids: bool
    high_id_namespace_excluded: bool


@dataclass(frozen=True, slots=True)
class CandidateContext:
    view: PreDecisionView
    game_patch: str
    map_id: int
    champion_id: int
    champion_key: str | None = None


@dataclass(frozen=True, slots=True)
class UserBudget:
    gold: int
    source: BudgetSource = BudgetSource.USER_PROVIDED


@dataclass(frozen=True, slots=True)
class Candidate:
    item_id: int
    remaining_cost: int
    affordability: Affordability
    evidence_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Exclusion:
    item_id: int
    reason_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CandidateResult:
    algorithm_version: str
    catalog_id: str
    rules_version: str
    candidates: tuple[Candidate, ...]
    exclusions: tuple[Exclusion, ...]
    diagnostic_codes: tuple[str, ...]


def _valid_int(value: Any, *, minimum: int = 0) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= minimum


def _recipe(item: Item) -> tuple[tuple[int, int], ...] | None:
    if not isinstance(item.recipe, list):
        return None
    counts: Counter[int] = Counter()
    for entry in item.recipe:
        if not isinstance(entry, dict):
            return None
        item_id, quantity = entry.get("itemId"), entry.get("quantity")
        if not _valid_int(item_id) or not _valid_int(quantity, minimum=1):
            return None
        counts[cast(int, item_id)] += cast(int, quantity)
    return tuple(sorted(counts.items()))


def _validate_closure(target: int, items: dict[int, Item]) -> bool:
    done: set[int] = set()
    visiting: set[int] = set()
    expanded: dict[int, int] = {}

    def visit(item_id: int) -> bool:
        if item_id in visiting:
            return False
        if item_id in done:
            return True
        item = items.get(item_id)
        if item is None or not _valid_int(item.price):
            return False
        recipe = _recipe(item)
        if recipe is None:
            return False
        visiting.add(item_id)
        total = 0
        units = 1
        for child_id, quantity in recipe:
            if quantity > MAX_RECIPE_EXPANSIONS or not visit(child_id):
                visiting.remove(item_id)
                return False
            total += items[child_id].price * quantity
            units += expanded[child_id] * quantity
            if units > MAX_RECIPE_EXPANSIONS:
                visiting.remove(item_id)
                return False
        visiting.remove(item_id)
        if total > item.price:
            return False
        done.add(item_id)
        expanded[item_id] = units
        return True

    return visit(target)


def _completion_options(
    target: int, items: dict[int, Item], owned: tuple[tuple[int, int], ...]
) -> dict[tuple[int, ...], int]:
    """Exact bounded search: each state is remaining inventory and minimum spend.

    Buy a ready node with one owned unit, or assemble its children and pay
    the nonnegative combine fee. This explores every valid component allocation
    (including keeping a ready component while using loose descendants).
    """
    ids = tuple(item_id for item_id, _ in owned)
    initial = tuple(count for _, count in owned)
    positions = {item_id: n for n, item_id in enumerate(ids)}
    memo: dict[tuple[int, tuple[int, ...]], dict[tuple[int, ...], int]] = {}

    def solve(item_id: int, state: tuple[int, ...]) -> dict[tuple[int, ...], int]:
        key = (item_id, state)
        if key in memo:
            return memo[key]
        item = items[item_id]
        options: dict[tuple[int, ...], int] = {}
        index = positions.get(item_id)
        if index is not None and state[index]:
            rest = list(state)
            rest[index] -= 1
            options[tuple(rest)] = 0
        recipe = _recipe(item)
        assert recipe is not None  # validated before search
        component_total = sum(items[child].price * count for child, count in recipe)
        states = {state: item.price - component_total}
        for child, count in recipe:
            for _ in range(count):
                following: dict[tuple[int, ...], int] = {}
                for available, cost in states.items():
                    for remainder, child_cost in solve(child, available).items():
                        total = cost + child_cost
                        following[remainder] = min(following.get(remainder, total), total)
                states = following
        for remainder, cost in states.items():
            options[remainder] = min(options.get(remainder, cost), cost)
        memo[key] = options
        return options

    return solve(target, initial)


def enumerate_candidates(
    context: CandidateContext,
    catalog: CatalogVersion,
    items: tuple[Item, ...],
    classification: ItemClassificationTable,
    rules: ItemRules,
    *,
    user_budget: UserBudget | None = None,
) -> CandidateResult:
    if (
        context.game_patch != catalog.game_patch
        or rules.game_patch != catalog.game_patch
        or rules.curation_rules_version != catalog.curation_rules_version
    ):
        raise CandidateInputError("CATALOG_PATCH_OR_RULES_MISMATCH")
    if (
        classification.classification_version != ITEM_CLASSIFICATION_VERSION
        or classification.curation_rules_version != catalog.curation_rules_version
    ):
        raise CandidateInputError("CLASSIFICATION_VERSION_MISMATCH")
    if not rules.version or not _valid_int(context.map_id) or not _valid_int(context.champion_id):
        raise CandidateInputError("INVALID_CONTEXT_OR_RULES")
    if user_budget is not None and (
        user_budget.source is not BudgetSource.USER_PROVIDED
        or not _valid_int(user_budget.gold)
        or user_budget.gold > 100_000
    ):
        raise CandidateInputError("INVALID_USER_BUDGET")
    by_id = {item.item_id: item for item in items}
    if len(by_id) != len(items) or any(item.catalog_id != catalog.id for item in items):
        raise CandidateInputError("CATALOG_ITEMS_MISMATCH")
    if not set(by_id).issubset(classification.by_item_id) or any(
        entry.item_class is not ItemClass.EXCLUDED
        for item_id, entry in classification.by_item_id.items()
        if item_id not in by_id
    ):
        raise CandidateInputError("INCOMPLETE_CLASSIFICATION_OR_CATALOG")
    if any(
        not _valid_int(item_id) or not _valid_int(count, minimum=1)
        for item_id, count in context.view.inventory_items
    ):
        raise CandidateInputError("INVALID_INVENTORY")
    owned = Counter(dict(context.view.inventory_items))
    if sum(owned.values()) > 6:
        raise CandidateInputError("INVALID_INVENTORY_SLOTS")
    # Unknown owned items prevent safe duplicate/group/slot reasoning.
    if any(
        classification.classify(item_id).item_class
        in {ItemClass.UNKNOWN, ItemClass.EXCLUDED, ItemClass.UNCLASSIFIED}
        for item_id in owned
    ):
        raise CandidateInputError("OWNED_ITEM_UNCLASSIFIABLE")
    if context.view.pre_decision_uncertain:
        raise CandidateInputError("PRE_DECISION_INVENTORY_UNCERTAIN")
    candidates: list[Candidate] = []
    exclusions: list[Exclusion] = []
    for item_id, entry in sorted(classification.by_item_id.items()):
        if item_id not in by_id:
            exclusions.append(Exclusion(item_id, (entry.rule_code,)))
    for item_id in sorted(by_id):
        item = by_id[item_id]
        reasons: list[str] = []
        item_class = classification.classify(item_id)
        if item_class.item_class is not ItemClass.MAIN:
            reasons.append(item_class.rule_code)
        if item_id in rules.excluded_ids or (
            rules.high_id_namespace_excluded and item_id >= 100_000
        ):
            reasons.append("RULE_EXCLUDED")
        if item.flags.get("purchasable") is not True:
            reasons.append("NOT_PURCHASABLE")
        maps = item.flags.get("maps")
        if not isinstance(maps, list) or context.map_id not in maps:
            reasons.append("MAP_ILLEGAL")
        required = item.flags.get("requiredChampion")
        if required is not None and (
            not isinstance(required, str) or required != context.champion_key
        ):
            reasons.append("REQUIRED_CHAMPION")
        if not rules.unique_item_ids:
            reasons.append("DUPLICATE_RULE_UNKNOWN")
        elif owned[item_id]:
            reasons.append("ALREADY_OWNED")
        if not rules.exclusive_coverage:
            reasons.append("EXCLUSIVE_GROUP_UNKNOWN")
        elif any(
            item_id in group and any(owned[other] for other in group if other != item_id)
            for group in rules.exclusive_groups
        ):
            reasons.append("EXCLUSIVE_GROUP_OWNED")
        if not _validate_closure(item_id, by_id):
            reasons.append("RECIPE_INCOMPATIBLE")
        if reasons:
            exclusions.append(Exclusion(item_id, tuple(dict.fromkeys(reasons))))
            continue
        options = _completion_options(item_id, by_id, tuple(sorted(owned.items())))
        feasible = [(cost, state) for state, cost in options.items() if sum(state) + 1 <= 6]
        if not feasible:
            exclusions.append(Exclusion(item_id, ("NO_INVENTORY_SLOT",)))
            continue
        cost, state = min(feasible)
        assert 0 <= cost <= item.price
        affordability = Affordability.UNKNOWN
        evidence = ["RECIPE_VALIDATED", "SLOT_AVAILABLE"]
        if state != tuple(count for _, count in sorted(owned.items())):
            evidence.append("OWNED_COMPONENT_COMPATIBLE")
        if user_budget is not None:
            affordability = (
                Affordability.WITHIN_USER_BUDGET
                if cost <= user_budget.gold
                else Affordability.ABOVE_USER_BUDGET
            )
            evidence.append("USER_PROVIDED_SCENARIO")
        elif context.view.gold.value is not None:
            evidence.append("FRAME_GOLD_ESTIMATE_NOT_EXACT")
        else:
            evidence.append("UNKNOWN_BUDGET")
        candidates.append(Candidate(item_id, cost, affordability, tuple(evidence)))
    return CandidateResult(
        CANDIDATE_ALGORITHM_VERSION,
        str(catalog.id),
        rules.version,
        tuple(candidates),
        tuple(sorted(exclusions, key=lambda entry: entry.item_id)),
        () if candidates else ("NO_LEGAL_CANDIDATES",),
    )
