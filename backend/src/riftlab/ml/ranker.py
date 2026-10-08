"""CC-402: B0/B1 baselines and the CatBoost PairLogit ranker (CPU), selected on validation.

At most 12 small configurations; early stopping uses the validation pool only.
The test split is never touched here: `evaluate_test` is a separate, single run.
"""

from __future__ import annotations

import resource
import time
from collections.abc import Mapping, Sequence
from typing import Any

from riftlab.dataset.popularity import PopularityModel
from riftlab.ml.features import Catalogs, categorical_indices, decision_vectors, design_matrix
from riftlab.ml.metrics import evaluate

# B1 (pesos fixados antes de qualquer avaliação, CC-402): aproveitar componentes vale 2,
# cada 1.000 de ouro ainda a pagar custa 1.
B1_OWNED_WEIGHT = 2.0
B1_COST_PER_GOLD = 1 / 1000
GRID = tuple(
    {"depth": depth, "learning_rate": rate, "l2_leaf_reg": l2}
    for depth in (4, 6)
    for rate in (0.05, 0.15)
    for l2 in (3.0, 10.0)
)
MAX_ITERATIONS = 2000
EARLY_STOPPING = 50
SEED = 402
assert len(GRID) <= 12


def b0_scores(model: PopularityModel) -> Any:
    def score(row: Mapping[str, Any]) -> list[float]:
        return [
            model.score(row["championId"], row["mainItemOrdinal"], c["itemId"])
            for c in row.get("candidates") or []
        ]

    return score


def b1_score(row: Mapping[str, Any]) -> list[float]:
    return [
        B1_OWNED_WEIGHT * float(bool(c.get("ownedComponentCompatible")))
        - B1_COST_PER_GOLD * float(c.get("remainingCost") or 0)
        for c in row.get("candidates") or []
    ]


def _pool(rows: Sequence[Mapping[str, Any]], catalogs: Catalogs, drop: frozenset[str]) -> Any:
    from catboost import Pool

    x, labels, groups, _, names = design_matrix(rows, catalogs, drop)
    return Pool(
        x,
        label=labels,
        group_id=groups,
        cat_features=categorical_indices(names),
        feature_names=list(names),
    )


def model_scores(model: Any, catalogs: Catalogs, drop: frozenset[str]) -> Any:
    from catboost import Pool

    def score(row: Mapping[str, Any]) -> list[float]:
        vectors = decision_vectors(row, catalogs)
        if not vectors:
            return []
        names = [n for n in model.feature_names_]
        x = [[vector[name] for name in names] for vector in vectors]
        cats = [
            i for i, name in enumerate(names) if name in ("self_champion_id", "candidate_item_id")
        ]
        return list(model.predict(Pool(x, cat_features=cats, feature_names=names)))

    return score


def train_and_select(
    train: Sequence[Mapping[str, Any]],
    validation: Sequence[Mapping[str, Any]],
    catalogs: Catalogs,
    drop: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """Train every grid configuration; pick the best validation MRR (ties: fewer trees)."""
    from catboost import CatBoostRanker

    train_rows = [r for r in train if r.get("candidates") and r.get("targetLegal")]
    train_pool, val_pool = _pool(train_rows, catalogs, drop), _pool(validation, catalogs, drop)
    results = []
    best: tuple[float, int, Any, dict[str, Any]] | None = None
    started = time.perf_counter()
    for config in GRID:
        model = CatBoostRanker(
            loss_function="PairLogit",
            iterations=MAX_ITERATIONS,
            od_type="Iter",
            od_wait=EARLY_STOPPING,
            random_seed=SEED,
            thread_count=1,
            verbose=False,
            allow_writing_files=False,
            **config,
        )
        t0 = time.perf_counter()
        model.fit(train_pool, eval_set=val_pool, use_best_model=True)
        report = evaluate(validation, model_scores(model, catalogs, drop), resamples=0)
        mrr = report["all"]["point"]["mrr"] or 0.0
        trees = model.tree_count_
        results.append(
            {
                **config,
                "trees": trees,
                "seconds": round(time.perf_counter() - t0, 2),
                "validationMrr": mrr,
            }
        )
        if best is None or (mrr, -trees) > (best[0], -best[1]):
            best = (mrr, trees, model, config)
    assert best is not None
    return {
        "model": best[2],
        "config": best[3],
        "grid": results,
        "trainSeconds": round(time.perf_counter() - started, 2),
        "maxRssMb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1),
    }
