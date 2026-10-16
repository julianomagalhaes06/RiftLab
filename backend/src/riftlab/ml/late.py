"""CC-701: build-conditioned baseline, shared offline predictors and support.

These experimental functions do not register a model or extend the served scope.
No labels, event-of-target time, gold or hidden enemy inventories enter vectors.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from riftlab.dataset.popularity import PopularityModel, fit
from riftlab.ml.features import CHAMPION_TAGS, ITEM_TAGS, Catalogs

LATE_FEATURE_VERSION = "late_context_v1"
BUILD_PRIOR_WEIGHT = 10
MIN_CELL_ROWS = 60
MIN_CELL_MATCHES = 10
CATEGORICAL = ("champion", "candidate")


def build_key(row: Mapping[str, Any]) -> tuple[int, int, tuple[int, ...]]:
    return row["championId"], row["mainItemOrdinal"], tuple(sorted(row["ownedMainItemIds"]))


@dataclass(frozen=True)
class BuildPopularity:
    base: PopularityModel
    builds: Mapping[tuple[int, int, tuple[int, ...]], Counter[int]]

    def score(self, row: Mapping[str, Any]) -> list[float]:
        counts = self.builds.get(build_key(row), Counter())
        total = sum(counts.values())
        return [
            (
                counts[c["itemId"]]
                + BUILD_PRIOR_WEIGHT
                * self.base.score(row["championId"], row["mainItemOrdinal"], c["itemId"])
            )
            / (total + BUILD_PRIOR_WEIGHT)
            for c in row["candidates"]
        ]


def fit_build(rows: Sequence[Mapping[str, Any]]) -> BuildPopularity:
    base = fit(rows)  # rejects validation/test even if an illegal target is present
    counts: dict[tuple[int, int, tuple[int, ...]], Counter[int]] = defaultdict(Counter)
    for row in rows:
        if row.get("targetLegal"):
            counts[build_key(row)][row["targetItemId"]] += 1
    return BuildPopularity(base, dict(counts))


def supported_cells(rows: Sequence[Mapping[str, Any]]) -> frozenset[tuple[int, int]]:
    counts: Counter[tuple[int, int]] = Counter()
    matches: dict[tuple[int, int], set[str]] = defaultdict(set)
    for row in rows:
        if row["split"] != "train":
            raise ValueError("suporte só pode usar treino")
        if not row.get("targetLegal"):
            continue
        cell = row["championId"], row["mainItemOrdinal"]
        counts[cell] += 1
        matches[cell].add(row["matchId"])
    return frozenset(
        c for c, n in counts.items() if n >= MIN_CELL_ROWS and len(matches[c]) >= MIN_CELL_MATCHES
    )


def vectors(
    row: Mapping[str, Any], catalogs: Catalogs, *, composition: bool = True
) -> list[dict[str, Any]]:
    """Fixed catalog vocabulary: no validation/test vocab fitting; null level stays NaN."""
    features = row["features"]
    inventory = dict(features["inventory_items"])
    level = features.get("level")
    context: dict[str, Any] = {
        "champion": str(row["championId"]),
        "ordinal": row["mainItemOrdinal"],
        "minute": features["game_time_ms"] / 60_000,
        "level": float(level) if level is not None else math.nan,
        "level_missing": int(level is None),
        **{f"owned_{i}": inventory.get(i, 0) for i in sorted(catalogs.items)},
    }
    if composition:
        for side in ("ally", "enemy"):
            champions = features.get(f"{side}_champion_ids")
            known = isinstance(champions, list) and all(
                c in catalogs.champion_tags for c in champions
            )
            context[f"{side}_missing"] = int(not known)
            for tag in CHAMPION_TAGS:
                context[f"{side}_{tag}"] = (
                    sum(tag in catalogs.champion_tags.get(c, ()) for c in champions)
                    if known
                    else math.nan
                )
    out = []
    for candidate in row["candidates"]:
        item_id = candidate["itemId"]
        price, tags = catalogs.items[item_id]
        out.append(
            {
                **context,
                "candidate": str(item_id),
                "price": price,
                **{f"tag_{t}": int(t in tags) for t in ITEM_TAGS},
            }
        )
    return out
