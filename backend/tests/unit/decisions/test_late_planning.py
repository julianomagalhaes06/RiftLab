"""CC-701: actual replay, early anchors, fail-closed quality and no future predictors."""

from __future__ import annotations

import copy
from dataclasses import replace

import pytest
from test_evaluation import (
    CATALOG_ITEMS,
    CATALOG_ROW,
    CURATION,
    MATCH,
    PARTICIPANTS,
    TABLE,
    _buy,
    _scope,
    _states,
)

from riftlab.candidates.domain import ItemRules
from riftlab.dataset.late import planning_rows
from riftlab.ml.features import Catalogs
from riftlab.ml.late import fit_build, supported_cells, vectors

RULES = ItemRules("test", "P-1", CURATION, frozenset(), (), True, True, True)


def history():
    # Three distinct MAIN, interleaved boots/consumables to create planning anchors.
    # Components exist in the catalog but are purchased completed in this fixture.
    return _states(
        [
            _buy(1, 1000, 3001),
            _buy(2, 2000, 1003),
            _buy(3, 10000, 3002),
            _buy(4, 11000, 2003),
            _buy(5, 20000, 3003),
        ]
    )


def extract(states):
    return planning_rows(
        states,
        match=MATCH,
        participants=PARTICIPANTS,
        catalog=CATALOG_ROW,
        items=tuple(CATALOG_ITEMS.values()),
        classification=TABLE,
        scope=_scope(),
        rules=RULES,
        split="train",
    )


def test_late_replay_anchors_before_target_with_gold_removed():
    rows, counts = extract(history())
    assert len(rows) == 1, counts
    row = rows[0]
    assert row["anchorEventSourceIndex"] == 4
    assert row["targetEventSourceIndex"] == 5
    assert row["anchorTimestampMs"] < row["eventTimestampMs"]
    assert row["ownedMainItemIds"] == [3001, 3002]
    assert 3001 not in row["candidateItemIds"] and 3002 not in row["candidateItemIds"]
    assert 3003 in row["candidateItemIds"]
    assert not any(k.startswith("gold_") for k in row["features"])
    assert set(row["featureMasks"]) == set(row["features"])


def test_invalid_first_anchor_does_not_slide_forward():
    states = history()
    anchor = copy.deepcopy(states[3])
    anchor.quality["preDecisionQuality"] = {"uncertain": True, "reasons": []}
    later = replace(states[3], event_source_index=40)
    # A later index is not eligible to replace the first uncertain anchor.
    target = replace(states[4], event_source_index=50)
    rows, counts = extract([*states[:3], anchor, later, target])
    assert rows == []
    assert counts["ANCHOR_UNCERTAIN"] == 1


def test_same_timestamp_is_not_prior_planning_and_missing_anchor_abstains():
    states = history()
    target = copy.deepcopy(states[-1])
    target.snapshot["eventTimestampMs"] = 11000
    assert extract([*states[:-1], target])[1]["ANCHOR_NOT_STRICTLY_EARLIER"] == 1
    assert extract([*states[:3], states[-1]])[1]["NO_PRIOR_ANCHOR"] == 1


def test_anchor_legal_target_not_forced_into_candidates():
    states = history()
    # Fill the anchor's six slots with unrelated inventory units.
    anchor = copy.deepcopy(states[3])
    anchor.snapshot["inventory"]["items"]["1003"] = 4
    rows, counts = extract([*states[:3], anchor, states[-1]])
    assert rows == []
    assert counts["TARGET_NOT_LEGAL_AT_ANCHOR"] == 1


def test_predictors_invariant_to_target_and_future_fields():
    row = extract(history())[0][0]
    catalogs = Catalogs(
        {p.champion_id: ("Marksman",) for p in PARTICIPANTS},
        {i: (item.price, ()) for i, item in CATALOG_ITEMS.items()},
    )
    before = vectors(row, catalogs)
    changed = {
        **row,
        "targetItemId": -1,
        "eventTimestampMs": 999999,
        "targetEventSourceIndex": 999,
        "win": True,
        "finalInventory": [999],
    }
    assert vectors(changed, catalogs) == before
    assert all("gold" not in key for key in before[0])
    assert before[0]["level_missing"] == 1


def test_build_conditioning_and_train_only_support():
    row = extract(history())[0][0]
    alternate = {**row, "ownedMainItemIds": [3001, 3003], "targetItemId": 3002}
    test = {**row, "candidates": [{"itemId": 3002}, {"itemId": 3003}]}
    model = fit_build([row, alternate])
    assert model.score(test)[1] > model.score(test)[0]
    assert (
        model.score({**test, "ownedMainItemIds": [3001, 3003]})[0]
        > model.score({**test, "ownedMainItemIds": [3001, 3003]})[1]
    )
    with pytest.raises(ValueError):
        fit_build([{**row, "split": "test"}])
    assert not supported_cells([row] * 60)  # repeated one match cannot create support
    supported = [{**row, "matchId": f"M{i}"} for i in range(60)]
    assert (row["championId"], 3) in supported_cells(supported)
    with pytest.raises(ValueError):
        supported_cells([{**row, "split": "validation"}])


def test_unknown_champion_composition_is_missing_not_zero():
    import math

    row = extract(history())[0][0]
    catalogs = Catalogs({}, {i: (item.price, ()) for i, item in CATALOG_ITEMS.items()})
    vector = vectors(row, catalogs)[0]
    assert vector["enemy_missing"] == 1
    assert math.isnan(vector["enemy_Tank"])
    assert "enemy_Tank" not in vectors(row, catalogs, composition=False)[0]
