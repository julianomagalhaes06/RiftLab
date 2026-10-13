"""CC-602: amostra cega e análise da revisão de especialistas, validadas com dados SINTÉTICOS.

Nenhuma linha aqui é real: partidas `SYN_*`, itens 1–20 e notas fabricadas para conferir a
mecânica (estratificação, cegamento, kappa e separação entre imitação e pertinência).
"""

from __future__ import annotations

import json
import math
from collections import Counter
from typing import Any

from riftlab.ml.expert_review import analyze, build_sample, quadratic_kappa, select_states


def _row(
    n: int, champion: int, ordinal: int, split: str = "test", target: int = 4
) -> dict[str, Any]:
    return {
        "matchId": f"SYN_{n}",
        "participantId": 4,
        "championId": champion,
        "mainItemOrdinal": ordinal,
        "split": split,
        "targetItemId": target,
        "targetLegal": True,
        "candidates": [{"itemId": i, "remainingCost": 100 * i} for i in range(1, 21)],
        "features": {
            "game_time_ms": 0 if ordinal == 1 else 840_000,
            "self_role": "BOTTOM",
            "level": 1 if ordinal == 1 else 9,
            "ally_champion_ids": [1, 2, 3, 4],
            "enemy_champion_ids": [5, 6, 7, 8, 9],
            "inventory_items": [] if ordinal == 1 else [[10, 2]],
        },
        "context": {
            "lane_opponent_champion_id": "5",
            "enemy_main_items": 2.0,
            "enemy_items_armor": 1.0,
            "kill_diff": -3.0,
            "team_gold_diff": math.nan,
        },
    }


def _rows() -> list[dict[str, Any]]:
    rows = [_row(i, 51, 1) for i in range(15)]
    rows += [_row(100 + i, 145, 1) for i in range(5)]
    rows += [_row(200 + i, 81, 2) for i in range(12)]
    rows += [_row(300 + i, 51, 1, split="train") for i in range(30)]
    return rows


def popularity(row: dict[str, Any]) -> list[float]:
    return [-float(c["itemId"]) for c in row["candidates"]]  # item 1 é o mais popular


def model(row: dict[str, Any]) -> list[float]:
    return [float(c["itemId"] == 7) for c in row["candidates"]]  # o modelo prefere o item 7


NAMES = {i: f"Item sintético {i}" for i in range(1, 21)}
CHAMPIONS = {i: f"Campeão sintético {i}" for i in range(1, 200)}
COSTS = {i: 1000 + i for i in range(1, 21)}


def _sample(**kwargs: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    return build_sample(
        _rows(), popularity, model, CHAMPIONS, NAMES, COSTS, synthetic=True, **kwargs
    )


def test_selection_is_stratified_capped_test_only_and_deterministic() -> None:
    chosen = select_states(_rows(), seed=602, per_stratum=12)
    strata = Counter((r["championId"], r["mainItemOrdinal"]) for r in chosen)
    assert strata == {(51, 1): 12, (145, 1): 5, (81, 2): 12}
    assert all(r["split"] == "test" for r in chosen)
    assert [r["matchId"] for r in chosen] == [
        r["matchId"] for r in select_states(_rows(), seed=602, per_stratum=12)
    ]
    assert [r["matchId"] for r in chosen] != [
        r["matchId"] for r in select_states(_rows(), seed=603, per_stratum=12)
    ]


def test_sheet_is_blind_and_the_key_keeps_every_origin() -> None:
    sheet, key = _sample()
    text = json.dumps(sheet, ensure_ascii=False)
    for leak in ("SYN_", "participant", "historical", "popularity", "model", "target", "control"):
        assert leak not in text, leak
    assert sheet["synthetic"] is True and len(sheet["states"]) == 29

    for state in sheet["states"]:
        roles = key["states"][state["stateId"]]["items"]
        listed = [str(i["itemId"]) for i in state["items"]]
        assert listed == list(roles)  # mesma ordem (embaralhada) na folha e na chave
        assert len(set(listed)) == len(listed) <= 6
        assert {r["popularityRank"] for r in roles.values()} >= {1, 2, 3}
        assert sum(r["historical"] for r in roles.values()) == 1
        assert sum(r["modelTop1"] for r in roles.values()) == 1
        [control] = [item for item, r in roles.items() if r["control"]]
        assert roles[control]["popularityRank"] is None and not roles[control]["historical"]
    orders = {tuple(i["itemId"] for i in s["items"]) for s in sheet["states"]}
    assert len(orders) > 1  # a posição do item não denuncia a origem

    first = sheet["states"][0]
    assert first["role"] == "rota inferior (atirador)"
    assert first["enemyItemTags"] == {"armadura": 1}
    assert first["teamGoldDiff"] is None and first["killDiff"] == -3.0  # NaN vira ausência
    second = next(s for s in sheet["states"] if s["mainItemOrdinal"] == 2)
    assert second["inventory"] == [{"name": "Item sintético 10", "quantity": 2}]


def test_quadratic_kappa_matches_a_hand_computed_case() -> None:
    assert quadratic_kappa([(0, 0), (1, 1), (2, 2), (3, 3)]) == 1.0
    # observado: 2 desacordos de 1 ponto (peso 1/9 cada); esperado: marginais uniformes
    # num = 2/9; den = (1/4) * soma de (i-j)²/9 em 4x4 = (1/4) * 40/9 = 10/9 → 1 - 0,2
    assert math.isclose(quadratic_kappa([(0, 0), (1, 1), (2, 3), (3, 2)]) or 0, 0.8)
    assert quadratic_kappa([(2, 2), (2, 2)]) is None  # sem variação esperada
    assert quadratic_kappa([]) is None


def _answers(key: dict[str, Any], shift: int = 0) -> dict[str, Any]:
    grades: dict[str, dict[str, int | None]] = {}
    for state, meta in key["states"].items():
        per_item: dict[str, int | None] = {}
        for item, role in meta["items"].items():
            grade = 3 if role["historical"] else 0 if role["control"] else 1
            per_item[item] = max(0, min(3, grade - shift))
        grades[state] = per_item
    first = next(iter(grades))
    grades[first][next(iter(grades[first]))] = None  # uma abstenção
    return {"profile": {"experience": "sintético"}, "grades": grades}


def test_analysis_separates_imitation_from_pertinence_with_synthetic_answers() -> None:
    _, key = _sample()
    report = analyze(key, {"rev-a": _answers(key), "rev-b": _answers(key)}, resamples=200)

    assert report["synthetic"] is True
    assert report["sample"]["states"] == 29 and report["sample"]["reviewers"] == 2
    assert report["agreement"]["weightedKappa"] == 1.0 and report["agreement"]["exact"] == 1.0
    assert [r["abstained"] for r in report["reviewers"]] == [1, 1]
    assert report["pertinence"]["historical"]["mean"] == 3.0
    assert report["pertinence"]["control"]["mean"] == 0.0
    assert report["pertinence"]["popularity_top1"]["mean"] == 1.0
    preference = report["preferences"]["popularityTop1_vs_historical"]
    assert preference["losses"] == preference["n"] > 0 and preference["meanDiff"] == -2.0
    # alvo sintético = item 4, fora do top-1 da popularidade: imitação zero, MRR 1/4
    assert report["imitation"] == {"popularityTop1IsHistorical": 0.0, "popularityMrrTop10": 0.25}
    assert "SYN_" not in json.dumps(report)


def test_disagreeing_reviewers_lower_kappa() -> None:
    _, key = _sample()
    report = analyze(key, {"rev-a": _answers(key), "rev-b": _answers(key, shift=1)}, resamples=200)
    agreement = report["agreement"]
    assert agreement["exact"] < 1.0 and agreement["withinOne"] == 1.0
    assert agreement["weightedKappa"] is not None and agreement["weightedKappa"] < 1.0
    low, high = agreement["ci95"]
    assert low <= agreement["weightedKappa"] <= high
