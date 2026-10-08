"""CC-402: metrics, features and a tiny CatBoost smoke run (synthetic data only)."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from riftlab.ml.features import ABLATIONS, COMPOSITION, Catalogs, decision_vectors, design_matrix
from riftlab.ml.metrics import bootstrap_by_match, evaluate, rank_of, summarize

CATALOGS = Catalogs(
    champion_tags={51: ("Marksman",), 12: ("Tank", "Support"), 99: ("Mage",)},
    items={
        3031: (3400, ("CriticalStrike", "Damage")),
        3006: (1100, ("AttackSpeed",)),
        6672: (3000, ("Damage",)),
    },
)


def _row(match: str, target: int, *, gold: int | None = 1500, seen: bool | None = None) -> dict:
    return {
        "matchId": match,
        "championId": 51,
        "mainItemOrdinal": 1,
        "targetItemId": target,
        "targetLegal": True,
        "playerSeenInTrain": seen,
        "candidates": [
            {"itemId": 3031, "remainingCost": 2100, "ownedComponentCompatible": True},
            {"itemId": 6672, "remainingCost": 3000, "ownedComponentCompatible": False},
        ],
        "features": {
            "gold_estimate": gold,
            "game_time_ms": 600_000,
            "level": 9,
            "gold_stale": False,
            "inventory_items": [[1036, 1]],
            "ally_champion_ids": [12],
            "enemy_champion_ids": [99, 12],
        },
    }


def test_rank_breaks_ties_by_item_id_and_metrics_are_exact() -> None:
    assert rank_of(5, [7, 5, 9], [1.0, 1.0, 0.0]) == 1
    assert rank_of(7, [7, 5, 9], [1.0, 1.0, 0.0]) == 2
    point = summarize([1, 2, 4])
    assert point["mrr"] == pytest.approx((1 + 0.5 + 0.25) / 3)
    assert point["ndcgAt3"] == pytest.approx((1 + 1 / math.log2(3) + 0) / 3)
    assert point["hitAt3"] == pytest.approx(2 / 3)


def test_bootstrap_keeps_a_match_together() -> None:
    ci = bootstrap_by_match([("m1", 1), ("m1", 1), ("m2", 3)], resamples=200, seed=1)
    low, high = ci["hitAt1"]
    assert 0.0 <= low <= high <= 1.0
    assert set(ci["mrr"]) <= {1.0, 1 / 3, (1 + 1 + 1 / 3) / 3, (1 / 3 + 1 / 3 + 1) / 3} | set(
        ci["mrr"]
    )


def test_evaluate_reports_player_disjoint_subset_and_skips_unlabelled() -> None:
    rows = [_row("m1", 3031, seen=False), _row("m2", 6672, seen=True), _row("m3", 9999)]
    report = evaluate(rows, lambda r: [1.0, 0.0], resamples=0)
    assert report["skipped"] == 1
    assert report["all"]["decisions"] == 2 and report["all"]["point"]["mrr"] == pytest.approx(0.75)
    assert (
        report["playerDisjoint"]["decisions"] == 1
        and report["playerDisjoint"]["point"]["mrr"] == 1.0
    )


def test_features_keep_missing_values_as_nan_and_derive_composition() -> None:
    vectors = decision_vectors(_row("m1", 3031, gold=None), CATALOGS)
    first = vectors[0]
    assert math.isnan(first["gold_estimate"]) and math.isnan(first["affordable_margin"])
    assert (first["enemy_mage"], first["enemy_tank"], first["ally_support"]) == (1.0, 1.0, 1.0)
    assert first["candidate_price"] == 3400 and first["item_criticalstrike"] == 1.0
    assert first["remaining_cost"] == 2100 and first["owned_component_compatible"] == 1.0


def test_ablation_drops_its_columns() -> None:
    _, labels, groups, _, names = design_matrix(
        [_row("m1", 3031)], CATALOGS, ABLATIONS["no_composition"]
    )
    assert labels == [1, 0] and groups == [0, 0]
    assert not set(COMPOSITION) & set(names)


def test_catboost_smoke_and_single_test_run(tmp_path: Path) -> None:
    pytest.importorskip("catboost")
    from riftlab.ml import ranker
    from riftlab.ml.experiment import ExperimentError, test_run, train_run

    rows = []
    for i in range(30):
        split = "train" if i < 20 else ("validation" if i < 25 else "test")
        rows.append({**_row(f"m{i}", 3031 if i % 3 else 6672, seen=False), "split": split})
    dataset = tmp_path / "dataset.json"
    dataset.write_text(json.dumps({"rows": rows}))
    champions = tmp_path / "champion.json"
    champions.write_text(json.dumps({"data": {"Caitlyn": {"key": "51", "tags": ["Marksman"]}}}))
    items = tmp_path / "item.json"
    items.write_text(
        json.dumps(
            {
                "data": {
                    "3031": {"gold": {"total": 3400}, "tags": ["Damage"]},
                    "6672": {"gold": {"total": 3000}, "tags": []},
                }
            }
        )
    )
    original_grid, original_iterations = ranker.GRID, ranker.MAX_ITERATIONS
    ranker.GRID, ranker.MAX_ITERATIONS = ranker.GRID[:1], 20
    try:
        report = train_run(dataset, champions, items, tmp_path / "run")
    finally:
        ranker.GRID, ranker.MAX_ITERATIONS = original_grid, original_iterations
    assert set(report["validation"]) == {
        "B0",
        "B1",
        "M1-full",
        "M1-no_composition",
        "M1-no_economy",
    }
    assert (tmp_path / "run" / "m1-full.cbm").exists()
    result = test_run(tmp_path / "run", dataset, champions, items)
    assert result["test"]["M1-full"]["all"]["decisions"] == 5
    with pytest.raises(ExperimentError, match="já executado"):
        test_run(tmp_path / "run", dataset, champions, items)
