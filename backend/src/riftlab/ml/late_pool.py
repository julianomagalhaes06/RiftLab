"""Bounded float32 matrices for late_context_v1; no training is performed here.

Avoid an N x features Python list and CatBoost's mixed-object conversion copies.
The default allocation cap intentionally refuses a full local dataset.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from riftlab.ml.features import Catalogs
from riftlab.ml.late import CATEGORICAL, vectors

DEFAULT_MAX_BYTES = 256 * 1024 * 1024


def matrix_size(rows: Sequence[Mapping[str, Any]], catalogs: Catalogs, composition: bool) -> int:
    if not rows:
        raise ValueError("dataset vazio")
    names = list(vectors(rows[0], catalogs, composition=composition)[0])
    lines = sum(len(row["candidates"]) for row in rows)
    # Numeric float32 + categorical object pointers + group int64 + label uint8.
    return lines * (4 * (len(names) - len(CATEGORICAL)) + 8 * len(CATEGORICAL) + 9)


def features_data(
    rows: Sequence[Mapping[str, Any]],
    catalogs: Catalogs,
    *,
    composition: bool = True,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> tuple[Any, Any, Any]:
    """Return CatBoost FeaturesData, labels, groups with a cap BEFORE allocating arrays."""
    required = matrix_size(rows, catalogs, composition)
    if required > max_bytes:
        raise MemoryError(f"matriz exige {required} bytes; teto explícito {max_bytes}")
    import numpy as np
    from catboost import FeaturesData

    first = vectors(rows[0], catalogs, composition=composition)[0]
    numeric = [n for n in first if n not in CATEGORICAL]
    lines = sum(len(row["candidates"]) for row in rows)
    numbers = np.empty((lines, len(numeric)), dtype=np.float32)
    categories = np.empty((lines, len(CATEGORICAL)), dtype=object)
    labels = np.empty(lines, dtype=np.uint8)
    groups = np.empty(lines, dtype=np.int64)
    encoded: dict[str, bytes] = {}
    pos = 0
    for group, row in enumerate(rows):
        for candidate, vector in zip(
            row["candidates"], vectors(row, catalogs, composition=composition), strict=True
        ):
            numbers[pos] = [vector[n] for n in numeric]
            for col, name in enumerate(CATEGORICAL):
                value = vector[name]
                if value not in encoded:
                    encoded[value] = value.encode("utf-8")
                categories[pos, col] = encoded[value]
            labels[pos] = int(candidate["itemId"] == row["targetItemId"])
            groups[pos] = group
            pos += 1
    return (
        FeaturesData(
            num_feature_data=numbers,
            cat_feature_data=categories,
            num_feature_names=numeric,
            cat_feature_names=list(CATEGORICAL),
        ),
        labels,
        groups,
    )


def make_pool(rows: Sequence[Mapping[str, Any]], catalogs: Catalogs, **kwargs: Any) -> Any:
    from catboost import Pool

    data, labels, groups = features_data(rows, catalogs, **kwargs)
    return Pool(data, label=labels, group_id=groups)


def scorer(model: Any, catalogs: Catalogs, composition: bool = True) -> Any:
    from catboost import Pool

    def score(row: Mapping[str, Any]) -> list[float]:
        data, _, _ = features_data([row], catalogs, composition=composition)
        return list(model.predict(Pool(data)))

    return score
