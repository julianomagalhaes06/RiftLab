"""CC-302: popularity baseline over legal candidates, fitted on the train split only.

Score of a candidate = smoothed frequency of that item as the historical target
for (champion, main item ordinal), backed off to the ordinal-wide frequency and
then to zero. Ties break by ascending item ID (documented, deterministic). The
metrics measure imitation of the historical choice, not whether it was a good
purchase.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

ALPHA = 1.0  # weight of the ordinal-wide prior inside each (champion, ordinal) cell
KS = (1, 3, 5)


@dataclass(frozen=True, slots=True)
class PopularityModel:
    by_cell: Mapping[tuple[int, int | None], Counter[int]]
    by_ordinal: Mapping[int | None, Counter[int]]

    def score(self, champion_id: int, ordinal: int | None, item_id: int) -> float:
        prior_counts = self.by_ordinal.get(ordinal, Counter())
        prior_total = sum(prior_counts.values())
        prior = prior_counts[item_id] / prior_total if prior_total else 0.0
        cell = self.by_cell.get((champion_id, ordinal), Counter())
        total = sum(cell.values())
        return (cell[item_id] + ALPHA * prior) / (total + ALPHA)

    def rank(self, champion_id: int, ordinal: int | None, candidates: Sequence[int]) -> list[int]:
        return sorted(
            candidates, key=lambda item_id: (-self.score(champion_id, ordinal, item_id), item_id)
        )


def fit(rows: Iterable[Mapping[str, Any]]) -> PopularityModel:
    by_cell: dict[tuple[int, int | None], Counter[int]] = defaultdict(Counter)
    by_ordinal: dict[int | None, Counter[int]] = defaultdict(Counter)
    for row in rows:
        if row["split"] != "train":
            raise ValueError("baseline só pode ser ajustado com linhas de treino")
        if not row.get("targetLegal"):
            continue  # an illegal or unlisted target never becomes a label
        by_cell[(row["championId"], row["mainItemOrdinal"])][row["targetItemId"]] += 1
        by_ordinal[row["mainItemOrdinal"]][row["targetItemId"]] += 1
    return PopularityModel(dict(by_cell), dict(by_ordinal))


def evaluate(model: PopularityModel, rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Hit@k / MRR of the baseline and of a uniform-random ranking (expected)."""
    counts: Counter[str] = Counter()
    hits = dict.fromkeys(KS, 0)
    random_hits = dict.fromkeys(KS, 0.0)
    reciprocal = random_reciprocal = 0.0
    seen_cell = 0
    for row in rows:
        counts["rows"] += 1
        candidates = row.get("candidateItemIds") or []
        if not candidates:
            counts["zeroCandidates"] += 1
            continue
        if not row.get("targetLegal"):
            counts["targetNotInCandidates"] += 1
            continue
        counts["evaluated"] += 1
        champion, ordinal, target = row["championId"], row["mainItemOrdinal"], row["targetItemId"]
        if (champion, ordinal) in model.by_cell:
            seen_cell += 1
        ranking = model.rank(champion, ordinal, candidates)
        position = ranking.index(target) + 1
        reciprocal += 1 / position
        size = len(candidates)
        random_reciprocal += sum(1 / k for k in range(1, size + 1)) / size
        for k in KS:
            hits[k] += position <= k
            random_hits[k] += min(k, size) / size
    evaluated = counts["evaluated"]

    def rate(value: float) -> float | None:
        return round(value / evaluated, 4) if evaluated else None

    return {
        "counts": dict(counts),
        "evaluatedWithSeenChampionOrdinal": seen_cell,
        "popularity": {
            **{f"hitAt{k}": rate(hits[k]) for k in KS},
            "mrr": rate(reciprocal),
        },
        "uniformRandomExpected": {
            **{f"hitAt{k}": rate(random_hits[k]) for k in KS},
            "mrr": rate(random_reciprocal),
        },
        "measures": "IMITATION_OF_HISTORICAL_CHOICE",
        "doesNotMeasure": "PERTINENCE_OR_PURCHASE_QUALITY",
    }
