"""CC-803 parte B: prontidão da popularidade própria contra a herdada (linhas sintéticas)."""

from __future__ import annotations

from typing import Any

from riftlab.ranking.engine import Popularity
from riftlab.reports.popularity_readiness import cell_support, evaluate_split, selection


def _row(
    split: str, champion: int, ordinal: int, target: int, candidates: list[int], match: str
) -> dict[str, Any]:
    return {
        "split": split,
        "championId": champion,
        "mainItemOrdinal": ordinal,
        "targetItemId": target,
        "candidateItemIds": candidates,
        "targetLegal": target in candidates,
        "matchId": match,
    }


def test_cell_support_counts_only_train_rows_with_legal_target() -> None:
    rows = [
        _row("train", 1, 1, 10, [10, 20], "M1"),
        _row("train", 1, 1, 99, [10, 20], "M2"),  # alvo ilegal
        _row("validation", 1, 1, 10, [10, 20], "M3"),
        _row("train", 2, 2, 20, [10, 20], "M4"),
    ]
    assert cell_support(rows) == {"1|1": 1, "2|2": 1}


def test_proposal_uses_own_where_supported_and_inherited_elsewhere() -> None:
    own = Popularity("own_v1", {"1|1": {"20": 5}}, game_patch="16.20")
    inherited = Popularity("old_v2", {"1|1": {"10": 5}, "2|1": {"10": 1}}, game_patch="16.19")
    rows = [
        _row("validation", 1, 1, 20, [10, 20], "M1"),  # própria acerta, herdada erra
        _row("validation", 2, 1, 10, [10, 20], "M2"),  # sem própria: herdada nos dois
        _row("validation", 3, 1, 10, [10, 20], "M3"),  # nenhuma referência: fora
        _row("validation", 1, 1, 30, [10, 20], "M4"),  # alvo fora dos candidatos
        _row("train", 1, 1, 20, [10, 20], "M5"),  # outra parte
    ]
    result = evaluate_split(rows, "validation", own, inherited)
    assert result["counts"] == {
        "evaluated": 2,
        "notEvaluable": 1,
        "rows": 4,
        "usingInheritedFallback": 1,
        "usingOwn": 1,
        "withoutAnyReference": 1,
    }
    assert result["proposal"] == {"mrr": 1.0, "hitAt1": 1.0}
    assert result["inheritedFiltered"] == {"mrr": 0.75, "hitAt1": 0.5}
    assert result["uniformExpected"] == {"mrr": 0.75, "hitAt1": 0.5}
    assert result["byCell"]["1|1"] == {"evaluated": 1, "usingOwn": 1}
    first = result["pairedBootstrapDeltaMrr"]
    assert first is not None and first["seed"] == 401 and first["resamples"] == 2000
    assert evaluate_split(rows, "validation", own, inherited)["pairedBootstrapDeltaMrr"] == first


def test_selection_follows_the_pre_registered_rule() -> None:
    def metrics(p_mrr: float | None, p_hit: float, i_mrr: float, i_hit: float) -> dict[str, Any]:
        return {
            "proposal": {"mrr": p_mrr, "hitAt1": p_hit},
            "inheritedFiltered": {"mrr": i_mrr, "hitAt1": i_hit},
        }

    assert selection(metrics(0.8, 0.6, 0.8, 0.6), 2)["decision"] == "ADOPT_OWN"  # empate
    assert selection(metrics(0.9, 0.5, 0.8, 0.6), 2)["decision"] == "KEEP_INHERITED"
    assert selection(metrics(0.7, 0.7, 0.8, 0.6), 2)["decision"] == "KEEP_INHERITED"
    assert selection(metrics(0.9, 0.9, 0.8, 0.6), 0)["decision"] == "NO_SAMPLE"
    assert selection(metrics(None, 0.0, 0.8, 0.6), 2)["decision"] == "NO_SAMPLE"
