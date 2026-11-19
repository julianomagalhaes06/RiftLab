"""CC-1504: escolha do artefato por patch e frases de gatilho com os números reais."""

from __future__ import annotations

from pathlib import Path

from riftlab.ranking.late_engine import TRIGGERS, _latest_dir


def test_nearest_patch_wins_and_ties_go_to_the_earlier(tmp_path: Path):
    for patch in ("16.19", "16.23"):
        (tmp_path / "late" / patch).mkdir(parents=True)
        (tmp_path / "late" / patch / "prior.json").write_text("{}")
    chosen = {p: _latest_dir(tmp_path, p)[0] for p in ("16.19", "16.20", "16.21", "16.22")}
    assert chosen == {"16.19": "16.19", "16.20": "16.19", "16.21": "16.19", "16.22": "16.23"}
    assert _latest_dir(tmp_path, "DEMO-1") is None


def test_trigger_sentences_use_context_numbers_and_skip_without_data():
    antiheal = TRIGGERS["m_antiheal_vs_sustain"]
    text = antiheal({"enemy_healers": 3.0, "enemy_items_sustain": 18.0}, "Lembrete Mortal")
    assert text == (
        "O time inimigo tem 3 campeões com cura no kit e 18% de vampirismo em itens; "
        "Lembrete Mortal aplica Feridas Dolorosas."
    )
    assert antiheal({"enemy_healers": 0.0, "enemy_items_sustain": 0.0}, "X") is None
    armor = TRIGGERS["m_armor_vs_physical"]
    assert armor({"enemy_magic_share": 0.8}, "X") is None  # dano mágico: frase de armadura não cabe
    assert "70% físico" in armor({"enemy_magic_share": 0.3, "enemy_items_ad": 250.0}, "X")
