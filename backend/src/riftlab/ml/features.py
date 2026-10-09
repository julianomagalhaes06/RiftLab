"""CC-402: one feature vector per (decision, legal candidate), from pre-decision data only.

Context comes from the decision point's stored features (no purchase, no future);
candidate attributes come from the pinned Data Dragon catalogs. Ally/enemy
composition is derived from champion tags. Missing values stay NaN, never zero.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from riftlab.ml.context import RICH_CONTEXT

CHAMPION_TAGS = ("Assassin", "Fighter", "Mage", "Marksman", "Support", "Tank")
ITEM_TAGS = (
    "Armor",
    "ArmorPenetration",
    "AttackSpeed",
    "CooldownReduction",
    "CriticalStrike",
    "Damage",
    "Health",
    "LifeSteal",
    "MagicPenetration",
    "Mana",
    "OnHit",
    "SpellBlock",
    "SpellDamage",
)
CATEGORICAL = (
    "self_champion_id",
    "candidate_item_id",
    "lane_opponent_champion_id",
    "decision_cluster",
)
COMPOSITION = tuple(f"{side}_{tag.lower()}" for side in ("ally", "enemy") for tag in CHAMPION_TAGS)
ECONOMY = ("gold_estimate", "gold_stale", "remaining_cost", "affordable_margin")
NUMERIC = (
    "main_item_ordinal",
    "game_time_min",
    "level",
    "inventory_units",
    "candidate_price",
    "owned_component_compatible",
    *ECONOMY,
    *(f"item_{tag.lower()}" for tag in ITEM_TAGS),
    *COMPOSITION,
    *(name for name in RICH_CONTEXT if name != "lane_opponent_champion_id"),
)
FEATURE_NAMES = (*CATEGORICAL, *NUMERIC)
ABLATIONS = {
    "full": frozenset(),
    "no_composition": frozenset(COMPOSITION),
    "no_economy": frozenset(ECONOMY),
    "no_rich_context": frozenset(RICH_CONTEXT),
}
CLUSTER_FEATURE = "decision_cluster"
NAN = math.nan


@dataclass(frozen=True, slots=True)
class Catalogs:
    """Pinned static data: champion key → tags; item id → (price, tags)."""

    champion_tags: Mapping[int, tuple[str, ...]]
    items: Mapping[int, tuple[int, tuple[str, ...]]]

    @classmethod
    def from_data_dragon(
        cls, champion_json: Mapping[str, Any], item_json: Mapping[str, Any]
    ) -> Catalogs:
        champions = {
            int(entry["key"]): tuple(entry.get("tags", ()))
            for entry in champion_json["data"].values()
        }
        items = {
            int(item_id): (int(entry["gold"]["total"]), tuple(entry.get("tags", ())))
            for item_id, entry in item_json["data"].items()
        }
        return cls(champions, items)


def _number(value: Any) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else NAN


def _composition(side: str, champion_ids: Any, catalogs: Catalogs) -> dict[str, float]:
    counts = dict.fromkeys(CHAMPION_TAGS, 0.0)
    if not isinstance(champion_ids, list):
        return {f"{side}_{tag.lower()}": NAN for tag in CHAMPION_TAGS}
    for champion in champion_ids:
        for tag in catalogs.champion_tags.get(champion, ()):
            if tag in counts:
                counts[tag] += 1
    return {f"{side}_{tag.lower()}": value for tag, value in counts.items()}


def _rich(context: Any) -> dict[str, Any]:
    """CC-408 context when present; NaN / "NONE" otherwise (never zero)."""
    context = context if isinstance(context, dict) else {}
    out: dict[str, Any] = {}
    for name in RICH_CONTEXT:
        if name == "lane_opponent_champion_id":
            out[name] = str(context.get(name, "NONE"))
        else:
            out[name] = _number(context.get(name))
    return out


def decision_vectors(row: Mapping[str, Any], catalogs: Catalogs) -> list[dict[str, Any]]:
    """Feature dicts for every legal candidate of one decision, in candidate order."""
    features = row.get("features") or {}
    gold = _number(features.get("gold_estimate"))
    inventory = features.get("inventory_items")
    units = float(sum(count for _, count in inventory)) if isinstance(inventory, list) else NAN
    stale = features.get("gold_stale")
    context = {
        "self_champion_id": str(row["championId"]),
        "main_item_ordinal": _number(row.get("mainItemOrdinal")),
        "game_time_min": _number(features.get("game_time_ms")) / 60_000,
        "level": _number(features.get("level")),
        "inventory_units": units,
        "gold_estimate": gold,
        "gold_stale": float(stale) if isinstance(stale, bool) else NAN,
        **_composition("ally", features.get("ally_champion_ids"), catalogs),
        **_composition("enemy", features.get("enemy_champion_ids"), catalogs),
        **_rich(row.get("context")),
        "decision_cluster": str(row.get("decisionCluster", "NONE")),
    }
    vectors = []
    for candidate in row.get("candidates") or []:
        price, tags = catalogs.items.get(candidate["itemId"], (None, ()))
        remaining = _number(candidate.get("remainingCost"))
        vectors.append(
            {
                **context,
                "candidate_item_id": str(candidate["itemId"]),
                "candidate_price": _number(price),
                "owned_component_compatible": float(
                    bool(candidate.get("ownedComponentCompatible"))
                ),
                "remaining_cost": remaining,
                "affordable_margin": gold - remaining,
                **{f"item_{tag.lower()}": float(tag in tags) for tag in ITEM_TAGS},
            }
        )
    return vectors


def design_matrix(
    rows: Iterable[Mapping[str, Any]], catalogs: Catalogs, drop: frozenset[str] = frozenset()
) -> tuple[list[list[Any]], list[int], list[int], list[int], tuple[str, ...]]:
    """X, labels, group ids, decision index per line, feature names (ablation drops columns)."""
    names = tuple(name for name in FEATURE_NAMES if name not in drop)
    x: list[list[Any]] = []
    labels: list[int] = []
    groups: list[int] = []
    owners: list[int] = []
    for index, row in enumerate(rows):
        for candidate, vector in zip(
            row.get("candidates") or [], decision_vectors(row, catalogs), strict=True
        ):
            x.append([vector[name] for name in names])
            labels.append(int(candidate["itemId"] == row["targetItemId"]))
            groups.append(index)
            owners.append(index)
    return x, labels, groups, owners, names


def categorical_indices(names: Sequence[str]) -> list[int]:
    return [index for index, name in enumerate(names) if name in CATEGORICAL]
