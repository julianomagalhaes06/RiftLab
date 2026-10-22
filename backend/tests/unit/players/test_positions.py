"""CC-1214: rota estimada na partida ao vivo (sem banco, sem rede)."""

from __future__ import annotations

import json
from pathlib import Path

from riftlab.players.positions import SMITE, TELEPORT, assign_positions, champion_roles

FLASH, IGNITE, HEAL, EXHAUST = 4, 14, 7, 3
KNOWN = {
    1: {"TOP": 900, "MIDDLE": 100},
    2: {"JUNGLE": 950, "TOP": 50},
    3: {"MIDDLE": 800, "UTILITY": 200},
    4: {"BOTTOM": 990, "MIDDLE": 10},
    5: {"UTILITY": 700, "MIDDLE": 300},
}


def test_most_likely_combination_in_any_riot_order() -> None:
    players = [
        (5, (FLASH, EXHAUST)),
        (4, (FLASH, HEAL)),
        (2, (SMITE, FLASH)),
        (3, (FLASH, IGNITE)),
        (1, (TELEPORT, FLASH)),
    ]
    assert assign_positions(players, KNOWN) == ["UTILITY", "BOTTOM", "JUNGLE", "MIDDLE", "TOP"]


def test_smite_decides_the_jungle_even_against_the_champion_habit() -> None:
    # Campeão 1 é de topo, mas quem leva Golpear é ele; o 2 (caçador) fica com o topo.
    players = [
        (1, (SMITE, FLASH)),
        (2, (TELEPORT, FLASH)),
        (3, (FLASH, IGNITE)),
        (4, (FLASH, HEAL)),
        (5, (FLASH, EXHAUST)),
    ]
    assert assign_positions(players, KNOWN) == ["JUNGLE", "TOP", "MIDDLE", "BOTTOM", "UTILITY"]


def test_unknown_champions_use_spells_and_other_modes_have_no_positions() -> None:
    players = [
        (901, (FLASH, HEAL)),
        (902, (SMITE, FLASH)),
        (903, (TELEPORT, FLASH)),
        (904, (FLASH, EXHAUST)),
        (905, (FLASH, IGNITE)),
    ]
    positions = assign_positions(players, {})
    assert positions is not None
    assert (positions[0], positions[1], positions[3]) == ("BOTTOM", "JUNGLE", "UTILITY")
    assert assign_positions(players[:3], KNOWN) is None


def test_reads_participations_from_the_reference(tmp_path: Path) -> None:
    reference = tmp_path / "reference.json"
    reference.write_text(
        json.dumps(
            {
                "participations": {
                    "51:BOTTOM": 30,
                    "51:MIDDLE": 2,
                    "x:TOP": 1,
                    "52:LANE": 4,
                    "53:TOP": 0,
                }
            }
        )
    )
    champion_roles.cache_clear()
    assert champion_roles(str(reference)) == {51: {"BOTTOM": 30, "MIDDLE": 2}}
    assert champion_roles(str(tmp_path / "nada.json")) == {}
