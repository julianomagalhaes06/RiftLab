"""CC-403: decision-context clusters (KMeans in numpy), fitted on the training split only.

The context vector is the decision's own pre-anchor context (composition counts,
main item ordinal, game time, rich context when present). The scaler and the
centroids come from training rows only; validation/test rows are only assigned.
Cluster IDs are opaque numbers, never presented as a tactical truth.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from riftlab.ml.features import COMPOSITION, Catalogs, decision_vectors

CONTEXT_FEATURES = (
    "main_item_ordinal",
    "game_time_min",
    "enemy_main_items",
    "team_gold_diff",
    "kill_diff",
    *COMPOSITION,
)


def context_matrix(rows: Sequence[Mapping[str, Any]], catalogs: Catalogs) -> np.ndarray:
    out = []
    for row in rows:
        vectors = decision_vectors(row, catalogs)
        base = vectors[0] if vectors else {}
        out.append([float(base.get(name, math.nan)) for name in CONTEXT_FEATURES])
    matrix = np.asarray(out, dtype=float)
    return np.where(np.isnan(matrix), 0.0, matrix) if matrix.size else matrix


@dataclass(frozen=True)
class ClusterModel:
    mean: np.ndarray
    scale: np.ndarray
    centroids: np.ndarray

    def assign(self, matrix: np.ndarray) -> np.ndarray:
        z = (matrix - self.mean) / self.scale
        distances = ((z[:, None, :] - self.centroids[None, :, :]) ** 2).sum(axis=2)
        return distances.argmin(axis=1)


def fit_kmeans(matrix: np.ndarray, k: int, *, seed: int, iterations: int = 100) -> ClusterModel:
    mean = matrix.mean(axis=0)
    scale = matrix.std(axis=0)
    scale = np.where(scale > 0, scale, 1.0)
    z = (matrix - mean) / scale
    rng = np.random.default_rng(seed)
    centroids = [z[rng.integers(len(z))]]
    for _ in range(1, k):  # k-means++
        d2 = np.min(((z[:, None, :] - np.asarray(centroids)[None]) ** 2).sum(axis=2), axis=1)
        probabilities = d2 / d2.sum() if d2.sum() > 0 else np.full(len(z), 1 / len(z))
        centroids.append(z[rng.choice(len(z), p=probabilities)])
    c = np.asarray(centroids)
    for _ in range(iterations):
        labels = ((z[:, None, :] - c[None]) ** 2).sum(axis=2).argmin(axis=1)
        new = np.asarray(
            [z[labels == j].mean(axis=0) if (labels == j).any() else c[j] for j in range(k)]
        )
        if np.allclose(new, c):
            break
        c = new
    return ClusterModel(mean, scale, c)


def silhouette(matrix: np.ndarray, model: ClusterModel) -> float:
    z = (matrix - model.mean) / model.scale
    labels = model.assign(matrix)
    distances = np.sqrt(((z[:, None, :] - z[None, :, :]) ** 2).sum(axis=2))
    scores = []
    for i, label in enumerate(labels):
        same = labels == label
        same[i] = False
        if not same.any():
            continue
        a = distances[i, same].mean()
        b = min(distances[i, labels == other].mean() for other in set(labels.tolist()) - {label})
        scores.append((b - a) / max(a, b) if max(a, b) > 0 else 0.0)
    return float(np.mean(scores)) if scores else math.nan


def stability(matrix: np.ndarray, k: int, seeds: Sequence[int]) -> float:
    """Mean pairwise agreement (adjusted for label permutation by best matching) across seeds."""
    labelings = [fit_kmeans(matrix, k, seed=s).assign(matrix) for s in seeds]
    agreements = []
    for i in range(len(labelings)):
        for j in range(i + 1, len(labelings)):
            a, b = labelings[i], labelings[j]
            # pair-counting agreement (Rand index), invariant to label names
            same_a = a[:, None] == a[None, :]
            same_b = b[:, None] == b[None, :]
            agreements.append(float((same_a == same_b).mean()))
    return float(np.mean(agreements)) if agreements else math.nan
