"""CC-403 clusters (numpy KMeans) and CC-404 support/abstention (synthetic)."""

from __future__ import annotations

import numpy as np

from riftlab.dataset.popularity import fit
from riftlab.ml.clusters import fit_kmeans, silhouette, stability
from riftlab.ml.support import (
    REASON_TEXT,
    SupportPolicy,
    cell_counts,
    risk_coverage,
    select_threshold,
)


def test_kmeans_separates_clear_groups_and_uses_the_training_scaler() -> None:
    rng = np.random.default_rng(0)
    train = np.vstack([rng.normal(0, 0.1, (30, 2)), rng.normal(5, 0.1, (30, 2))])
    model = fit_kmeans(train, 2, seed=1)
    labels = model.assign(train)
    assert len(set(labels[:30].tolist())) == 1 and labels[0] != labels[-1]
    assert silhouette(train, model) > 0.8
    assert stability(train, 2, seeds=(1, 2, 3)) == 1.0
    far = model.assign(np.array([[100.0, 100.0]]))  # assigned with the train scaler, no refit
    assert far[0] == labels[-1]


def _row(champion: int, ordinal: int, target: int, split: str = "validation", **kw: object) -> dict:
    return {
        "split": split,
        "championId": champion,
        "mainItemOrdinal": ordinal,
        "targetItemId": target,
        "targetLegal": True,
        "candidates": [{"itemId": i} for i in (10, 20, 30, 40)],
        "candidateItemIds": [10, 20, 30, 40],
        **kw,
    }


def test_validity_comes_before_support_and_reasons_are_readable() -> None:
    policy = SupportPolicy("16.19", 2, {(51, 1): 5})
    assert policy.decide(_row(51, 1, 10)) == (True, [])
    assert policy.decide(_row(51, 1, 10, status="PRE_DECISION_INVENTORY_UNCERTAIN"))[1] == [
        "INVALID_STATE"
    ]
    assert policy.decide({**_row(51, 1, 10), "candidates": [], "candidateItemIds": []})[1] == [
        "NO_LEGAL_CANDIDATES"
    ]
    assert policy.decide(_row(51, 1, 10, patch="16.20")) == (False, ["PATCH_UNSUPPORTED"])
    assert policy.decide(_row(99, 1, 10)) == (False, ["UNKNOWN_CHAMPION"])
    assert policy.decide(_row(51, 2, 10)) == (False, ["LOW_SUPPORT"])
    assert set(REASON_TEXT) >= {
        "INVALID_STATE",
        "LOW_SUPPORT",
        "UNKNOWN_CHAMPION",
        "PATCH_UNSUPPORTED",
    }


def test_threshold_is_chosen_on_validation_with_coverage_and_risk_together() -> None:
    train = [_row(51, 1, 10, "train") for _ in range(30)] + [
        _row(81, 1, 20, "train") for _ in range(3)
    ]
    model = fit(train)
    counts = cell_counts(train)
    validation = [_row(51, 1, 10) for _ in range(8)] + [_row(81, 1, 40) for _ in range(4)]
    threshold, curve = select_threshold(validation, model, counts, "16.19")
    full = risk_coverage(validation, model, SupportPolicy("16.19", 0, counts))
    assert full["coverage"] == 1.0 and full["risk"] > 0.15
    assert threshold == 5
    chosen = next(c for c in curve if c["threshold"] == threshold)
    assert chosen["coverage"] == 8 / 12 and chosen["risk"] == 0.0
    assert chosen["abstentionReasons"] == {"LOW_SUPPORT": 4}
