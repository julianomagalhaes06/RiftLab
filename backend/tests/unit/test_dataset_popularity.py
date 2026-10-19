"""CC-302 popularity baseline and split assignment (pure, synthetic rows)."""

from __future__ import annotations

from typing import Any

import pytest

from riftlab.dataset.popularity import evaluate, fit
from riftlab.dataset.rows import RowsError, split_assignment


def row(split: str, champ: int, ordinal: int, target: int, cands: list[int], **kw: Any) -> dict:
    return {
        "split": split,
        "championId": champ,
        "mainItemOrdinal": ordinal,
        "targetItemId": target,
        "candidateItemIds": cands,
        "targetLegal": kw.get("legal", target in cands),
    }


def test_fit_refuses_test_rows_so_the_baseline_never_sees_test() -> None:
    with pytest.raises(ValueError, match="treino"):
        fit([row("test", 1, 1, 10, [10])])


def test_ranking_uses_champion_cell_then_ordinal_prior_then_item_id() -> None:
    model = fit(
        [
            row("train", 1, 1, 10, [10, 20, 30]),
            row("train", 1, 1, 10, [10, 20, 30]),
            row("train", 2, 1, 20, [10, 20, 30]),
            row("train", 2, 1, 20, [10, 20, 30]),
            row("train", 2, 1, 20, [10, 20, 30]),
        ]
    )

    assert model.rank(1, 1, [30, 20, 10]) == [10, 20, 30]  # own cell wins
    assert model.rank(9, 1, [30, 20, 10]) == [20, 10, 30]  # unseen champion: ordinal prior
    assert model.rank(9, 2, [30, 20, 10]) == [10, 20, 30]  # nothing known: item id fallback


def test_illegal_targets_are_never_labels() -> None:
    model = fit([row("train", 1, 1, 99, [10, 20], legal=False)])

    assert model.by_cell == {}


def test_evaluate_reports_missing_targets_zero_candidates_and_random_expectation() -> None:
    model = fit([row("train", 1, 1, 10, [10, 20])])
    report = evaluate(
        model,
        [
            row("test", 1, 1, 10, [10, 20]),  # rank 1
            row("test", 1, 1, 20, [10, 20]),  # rank 2
            row("test", 1, 1, 30, [10, 20]),  # target not legal
            row("test", 1, 1, 10, []),  # zero candidates
        ],
    )

    assert report["counts"] == {
        "rows": 4,
        "evaluated": 2,
        "targetNotInCandidates": 1,
        "zeroCandidates": 1,
    }
    assert report["popularity"]["hitAt1"] == 0.5
    assert report["popularity"]["mrr"] == 0.75
    assert report["uniformRandomExpected"]["hitAt1"] == 0.5
    assert report["uniformRandomExpected"]["mrr"] == 0.75
    assert report["measures"] == "IMITATION_OF_HISTORICAL_CHOICE"


def test_split_assignment_rejects_overlap() -> None:
    manifest = {"split": {"trainMatchIds": ["BR1_1"], "testMatchIds": ["BR1_2"]}}
    assert split_assignment(manifest) == {"BR1_1": "train", "BR1_2": "test"}
    with pytest.raises(RowsError):
        split_assignment({"split": {"trainMatchIds": ["BR1_1"], "testMatchIds": ["BR1_1"]}})


def test_planning_anchor_never_lies_after_the_target() -> None:
    from riftlab.dataset.rows import planning_anchor

    states = [3, 10, 20, 35, 50]
    assert planning_anchor(states, ordinal=1, first_main_index=None, target_index=35) == 3
    assert planning_anchor(states, ordinal=2, first_main_index=20, target_index=50) == 35
    assert planning_anchor(states, ordinal=2, first_main_index=35, target_index=50) == 50
    assert planning_anchor(states, ordinal=2, first_main_index=None, target_index=50) is None
    assert planning_anchor(states, ordinal=2, first_main_index=50, target_index=50) is None


def test_export_cells_keeps_only_train_legal_counts_and_feeds_the_served_engine() -> None:
    from riftlab.dataset.popularity import export_cells
    from riftlab.ranking.engine import DecisionFacts, Popularity

    rows = [
        {**row("train", 222, 1, 10, [10, 20]), "matchId": "BR1_1"},
        {**row("train", 222, 1, 10, [10, 20]), "matchId": "BR1_2"},
        {**row("train", 222, 1, 20, [10, 20]), "matchId": "BR1_3"},
        {**row("train", 222, 2, 99, [10, 20]), "matchId": "BR1_4"},  # illegal target
        {**row("test", 222, 1, 20, [10, 20]), "matchId": "BR1_5"},  # never counted
    ]

    document = export_cells(
        rows, version="popularity_test_v2", game_patch="16.19", source="s", dataset_sha256="0" * 64
    )

    assert document["cells"] == {"222|1": {"10": 2, "20": 1}}
    assert "BR1_" not in str(document)
    served = Popularity.load_document(document)
    assert served.support(DecisionFacts(222, 1, None, None)) == 3


def test_cli_export_popularity_never_overwrites_a_served_version(tmp_path: Any) -> None:
    import hashlib
    import json

    import typer

    from riftlab.cli.main import dataset_export_popularity

    dataset = tmp_path / ".private" / "dataset.json"
    dataset.parent.mkdir()
    raw = json.dumps({"patch": "16.19", "rows": [row("train", 22, 1, 10, [10])]}).encode()
    dataset.write_bytes(raw)
    dataset.with_suffix(".json.sha256").write_text(
        f"{hashlib.sha256(raw).hexdigest()}  {dataset.name}\n", encoding="ascii"
    )
    output = tmp_path / "popularity-16.19-v9.json"

    dataset_export_popularity(
        dataset=dataset, output=output, version="v9", source="teste", min_cell_support=0
    )
    exported = json.loads(output.read_text(encoding="utf-8"))
    assert exported["cells"] == {"22|1": {"10": 1}}
    assert exported["datasetSha256"] == hashlib.sha256(raw).hexdigest()

    with pytest.raises(typer.Exit):
        dataset_export_popularity(
            dataset=dataset, output=output, version="v9", source="teste", min_cell_support=0
        )


def test_export_cells_minimum_drops_small_cells_and_default_keeps_the_cc704_shape() -> None:
    from riftlab.dataset.popularity import export_cells

    rows = [{**row("train", 222, 1, 10, [10, 20]), "matchId": f"BR1_{i}"} for i in range(3)] + [
        {**row("train", 51, 1, 20, [10, 20]), "matchId": "BR1_9"}
    ]
    kwargs = {"version": "v", "game_patch": "16.20", "source": "s", "dataset_sha256": "0" * 64}
    default = export_cells(rows, **kwargs)
    assert "minCellSupport" not in default
    assert set(default["cells"]) == {"222|1", "51|1"}
    assert export_cells(rows, min_cell_support=0, **kwargs) == default
    strict = export_cells(rows, min_cell_support=3, **kwargs)
    assert strict["minCellSupport"] == 3
    assert strict["cells"] == {"222|1": {"10": 3}}
