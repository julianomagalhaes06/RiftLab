"""CC-1505: cartões por gatilho, busca BM25, agregados com amostra mínima e validador."""

from __future__ import annotations

import json
from pathlib import Path

from riftlab.knowledge.base import (
    MIN_CHAMPION_CASES,
    Bm25,
    Chunk,
    KnowledgeBase,
    build_cases,
    load_cards,
    plain,
)
from riftlab.knowledge.explain import Fact, deterministic_answer, validate_answer
from riftlab.ml.late_gate import decide_gate

ROOT = Path(__file__).resolve().parents[3]
if not (ROOT / "configs").is_dir():
    ROOT = Path(__file__).resolve().parents[2]
CARDS = ROOT / "configs" / "knowledge" / "cards-v1.yaml"


def test_cards_trigger_only_when_condition_holds_and_item_fits():
    _, cards = load_cards(CARDS)
    antiheal = next(c for c in cards if c.id == "antiheal")
    assert antiheal.triggered({"enemy_healers": 2.0})
    assert not antiheal.triggered({"enemy_healers": 1.0, "enemy_items_sustain": 0.0})
    assert not antiheal.triggered({"enemy_healers": float("nan")})
    assert antiheal.fits({"ANTIHEAL"}, {}) and not antiheal.fits({"STASIS"}, {"ad": 60})
    armor = next(c for c in cards if c.id == "armor_vs_physical")
    assert armor.fits(set(), {"armor": 45.0}) and not armor.fits(set(), {"armor": 10.0})


def test_plain_text_and_bm25_prefer_matching_chunks():
    assert plain("<stats>35 Dano</stats><br><br>Feridas") == "35 Dano. Feridas."
    chunks = [
        Chunk("item:1", "ITEM", "Lembrete Mortal", "Aplica Feridas Dolorosas e penetração."),
        Chunk("item:2", "ITEM", "Ampulheta", "Entra em Estase por alguns segundos."),
    ]
    found = Bm25(chunks).search("feridas contra cura", limit=1)
    assert [c.id for c in found] == ["item:1"]


def row(target: int, champion: int = 7, split: str = "train") -> dict:
    return {
        "split": split,
        "targetLegal": True,
        "championId": champion,
        "role": "TOP",
        "ordinal": 5,
        "targetItemId": target,
        "context": {"enemy_healers": 3.0},
    }


def test_cases_keep_only_cells_with_minimum_sample(tmp_path: Path):
    rows = [row(10)] * (MIN_CHAMPION_CASES - 1) + [row(20, champion=8)] * MIN_CHAMPION_CASES
    cases = build_cases(rows + [row(99, split="validation")])
    assert "7|5|cura" not in cases["champion"] and cases["champion"]["8|5|cura"]["_n"] == 20
    (tmp_path / "knowledge.json").write_text(json.dumps({"patch": "16.19", "chunks": []}))
    (tmp_path / "cases.json").write_text(json.dumps(cases))
    kb = KnowledgeBase.load(tmp_path, CARDS)
    fact = kb.case_fact(8, "TOP", 5, {"enemy_healers": 3.0}, 20, {20: "Item X"})
    assert (
        fact is not None and "Em 20 decisões parecidas" in fact["text"] and "100%" in fact["text"]
    )
    assert "card:antiheal" in kb.chunks


FACTS = [
    Fact(
        "analysis:summary",
        "ANALYSIS",
        "Resultado",
        "Para esta partida, o 5º item é Lembrete Mortal (41%).",
    ),
    Fact("trigger:3033:m", "TRIGGER", "Gatilho", "O time inimigo tem 3 campeões com cura no kit."),
    Fact("card:antiheal", "CARD", "Cura", "Feridas Dolorosas reduzem a cura. Fonte: oficial."),
]


def test_validator_keeps_grounded_sentences_and_drops_the_rest():
    raw = json.dumps(
        {
            "frases": [
                {
                    "texto": "Com 3 campeões que se curam, Lembrete Mortal ajuda.",
                    "fontes": ["trigger:3033:m", "analysis:summary"],
                },
                {"texto": "Ele dá 80% de vitória.", "fontes": ["analysis:summary"]},
                {"texto": "Compre Ampulheta de Zhonya.", "fontes": ["card:antiheal"]},
                {"texto": "Feridas cortam cura.", "fontes": ["inventado"]},
            ]
        }
    )
    valid, dropped = validate_answer(raw, FACTS, ["Lembrete Mortal", "Ampulheta de Zhonya"])
    assert [v["sources"] for v in valid] == [["trigger:3033:m", "analysis:summary"]]
    assert dropped == ["NUMBER_NOT_IN_SOURCES", "NAME_NOT_IN_SOURCES", "UNKNOWN_SOURCE"]
    assert validate_answer("não é json", FACTS, []) == ([], ["INVALID_JSON"])
    answer = deterministic_answer(FACTS)
    assert answer[0]["sources"] == ["analysis:summary"] and "Fonte" not in answer[-1]["text"]


def test_gate_requires_all_three_rules():
    def report(nll_hi: float, dev_lo: float, late_hit: float, late_nll: float) -> dict:
        return {
            "all": {"ci95": {"delta_nll": [-0.2, nll_hi]}},
            "deviation": {"ci95": {"delta_hit3": [dev_lo, 0.1]}},
            "ordinal:5+": {
                "point": {
                    "model_hit3": late_hit,
                    "base_hit3": 0.5,
                    "model_nll": late_nll,
                    "base_nll": 2.0,
                }
            },
            "deviationClaims": {"observedRate": 0.3, "expectedByBase": 0.2},
        }

    passed = decide_gate(report(-0.01, 0.02, 0.55, 1.9))
    assert passed["passed"] and passed["claimStrong"]
    assert not decide_gate(report(0.01, 0.02, 0.55, 1.9))["passed"]
    assert not decide_gate(report(-0.01, -0.01, 0.55, 1.9))["passed"]
    assert not decide_gate(report(-0.01, 0.02, 0.48, 1.9))["passed"]


def test_validator_drops_invented_reasoning_even_with_grounded_numbers():
    facts = [Fact("item:3036", "ITEM", "Lembranças", "Lembranças: 35% de Penetração de Armadura.")]
    raw = json.dumps(
        {
            "frases": [
                {
                    "texto": "Com 35% de Penetração de Armadura, protege na defesa.",
                    "fontes": ["item:3036"],
                },
                {"texto": "Dá 35% de Penetração de Armadura.", "fontes": ["item:3036"]},
            ]
        }
    )
    valid, dropped = validate_answer(raw, facts, [])
    assert [v["text"] for v in valid] == ["Dá 35% de Penetração de Armadura."]
    assert dropped == ["WORDS_NOT_IN_SOURCES"]
