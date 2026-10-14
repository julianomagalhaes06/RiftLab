"""CC-402: ranking metrics for one relevant item per decision, with bootstrap by match."""

from __future__ import annotations

import math
import random
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from typing import Any

KS = (1, 3, 5)


def rank_of(target: int, candidates: Sequence[int], scores: Sequence[float]) -> int:
    """1-based rank of the target; ties break by ascending item id (deterministic)."""
    order = sorted(zip(candidates, scores, strict=True), key=lambda pair: (-pair[1], pair[0]))
    return [item for item, _ in order].index(target) + 1


def summarize(ranks: Sequence[int]) -> dict[str, float | None]:
    n = len(ranks)
    if not n:
        return {"mrr": None, "ndcgAt3": None, **{f"hitAt{k}": None for k in KS}}
    return {
        "mrr": sum(1 / r for r in ranks) / n,
        "ndcgAt3": sum((1 / math.log2(r + 1)) if r <= 3 else 0.0 for r in ranks) / n,
        **{f"hitAt{k}": sum(r <= k for r in ranks) / n for k in KS},
    }


def bootstrap_by_match(
    ranks: Sequence[tuple[str, int]], *, resamples: int, seed: int, confidence: float = 0.95
) -> dict[str, list[float]]:
    """Percentile intervals resampling MATCHES (decisions of a match stay together)."""
    by_match: dict[str, list[int]] = defaultdict(list)
    for match_id, rank in ranks:
        by_match[match_id].append(rank)
    matches = sorted(by_match)
    if not matches:
        return {}
    rng = random.Random(seed)
    samples: dict[str, list[float]] = defaultdict(list)
    for _ in range(resamples):
        drawn = [r for _ in matches for r in by_match[matches[rng.randrange(len(matches))]]]
        for name, value in summarize(drawn).items():
            assert value is not None
            samples[name].append(value)
    low, high = (1 - confidence) / 2, 1 - (1 - confidence) / 2
    return {
        name: [
            sorted(values)[int(low * (len(values) - 1))],
            sorted(values)[int(high * (len(values) - 1))],
        ]
        for name, values in samples.items()
    }


def evaluate(
    rows: Sequence[Mapping[str, Any]],
    score: Callable[[Mapping[str, Any]], Sequence[float]],
    *,
    resamples: int = 2000,
    seed: int = 401,
) -> dict[str, Any]:
    """Metrics over evaluable decisions (legal target, >=1 candidate): all, player-disjoint and
    the slices a frozen dataset declares per row in `subsets` (CC-702: champion slices), so a
    single test run reports every slice."""
    per_subset: dict[str, list[tuple[str, int]]] = {"all": [], "playerDisjoint": []}
    skipped = 0
    for row in rows:
        candidates = [c["itemId"] for c in row.get("candidates") or []]
        if not candidates or row["targetItemId"] not in candidates:
            skipped += 1
            continue
        rank = rank_of(row["targetItemId"], candidates, score(row))
        per_subset["all"].append((row["matchId"], rank))
        if row.get("playerSeenInTrain") is False:
            per_subset["playerDisjoint"].append((row["matchId"], rank))
        for name in row.get("subsets") or ():
            if name in ("all", "playerDisjoint"):
                raise ValueError(f"subset reservado: {name}")
            per_subset.setdefault(name, []).append((row["matchId"], rank))
    return {
        "skipped": skipped,
        **{
            subset: {
                "decisions": len(ranks),
                "matches": len({m for m, _ in ranks}),
                "point": summarize([r for _, r in ranks]),
                "ci95": bootstrap_by_match(ranks, resamples=resamples, seed=seed),
            }
            for subset, ranks in per_subset.items()
        },
    }
