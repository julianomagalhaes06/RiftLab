"""Small matrix contract checks only: never fit a model or allocate the full dataset."""

import math

import numpy as np
import pytest
from test_late_planning import CATALOG_ITEMS, PARTICIPANTS, extract, history

from riftlab.ml.features import Catalogs
from riftlab.ml.late import vectors
from riftlab.ml.late_pool import features_data, make_pool, matrix_size


def fixture():
    return extract(history())[0][0], Catalogs(
        {p.champion_id: ("Marksman",) for p in PARTICIPANTS},
        {i: (item.price, ()) for i, item in CATALOG_ITEMS.items()},
    )


def test_float32_preserves_predictors_and_missingness_with_cat_columns_named():
    row, catalogs = fixture()
    data, labels, groups = features_data([row], catalogs)
    expected = vectors(row, catalogs)
    assert data.num_feature_data.dtype == np.float32
    assert data.num_feature_data.flags.c_contiguous
    assert data.cat_feature_names == ["champion", "candidate"]
    for index, vector in enumerate(expected):
        for col, name in enumerate(data.num_feature_names):
            value = data.num_feature_data[index, col]
            if isinstance(vector[name], float) and math.isnan(vector[name]):
                assert math.isnan(value)
            else:
                assert value == pytest.approx(vector[name], rel=1e-6)
        assert data.cat_feature_data[index, 1] == vector["candidate"].encode()
        assert labels[index] == int(row["candidates"][index]["itemId"] == row["targetItemId"])
    assert groups.tolist() == [0] * len(expected)
    assert make_pool([row], catalogs).get_feature_names() == (
        data.num_feature_names + data.cat_feature_names
    )


def test_memory_cap_refuses_before_allocation_and_ablation_removes_composition():
    row, catalogs = fixture()
    with pytest.raises(MemoryError):
        features_data([row], catalogs, max_bytes=1)
    assert matrix_size([row], catalogs, False) < matrix_size([row], catalogs, True)
    data, _, _ = features_data([row], catalogs, composition=False)
    assert not any(n.startswith("enemy_") or n.startswith("ally_") for n in data.num_feature_names)
