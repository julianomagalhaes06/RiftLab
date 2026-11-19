"""CC-1502: perfis do Data Dragon e contexto situacional (sem nada da âncora em diante)."""

from __future__ import annotations

import math

from riftlab.decisions.domain import ParticipantView
from riftlab.matches.timeline import GoldFrame, ItemEvent, Kill, Objective, ParticipantTimeline
from riftlab.ml.situation import SituationInput, owned_main_items, situation_features
from riftlab.profiles.domain import (
    ChampionProfile,
    ItemProfile,
    parse_champion_profiles,
    parse_item_mechanics,
    parse_item_stats,
)

MORTAL = (
    "<mainText><stats><attention>35</attention> Attack Damage<br><attention>30%</attention>"
    " Armor Penetration<br><attention>25%</attention> Critical Strike Chance</stats><br><br>"
    "<passive>Grievous Wounds</passive><br>Dealing physical damage applies <keyword>40% Wounds"
    "</keyword> to enemy champions for 3 seconds.</mainText>"
)


def test_item_stats_come_from_the_stats_block_including_text_only_attributes():
    assert parse_item_stats(MORTAL) == {"ad": 35.0, "armor_pen": 30.0, "crit": 25.0}
    assert parse_item_mechanics(MORTAL) == frozenset({"ANTIHEAL"})
    zhonya = (
        "<stats><attention>105</attention> Ability Power</stats>"
        "<active>Time Stop</active>Enter <keyword>Stasis</keyword>"
    )
    assert "STASIS" in parse_item_mechanics(zhonya)
    edge = (
        "<stats></stats><passive>Annul</passive>"
        "Grants a Spell Shield that blocks the next enemy Ability."
    )
    assert parse_item_mechanics(edge) == frozenset({"SPELL_SHIELD"})


def test_champion_healing_ignores_sentences_that_reduce_healing():
    document = {
        "data": {
            "Healer": {
                "key": "1",
                "tags": ["Support"],
                "info": {"attack": 2, "defense": 5, "magic": 7, "difficulty": 3},
                "stats": {"attackrange": 550},
                "passive": {"name": "P", "description": "Moves faster."},
                "spells": [{"name": "Heal", "description": "Heals an ally champion."}],
            },
            "Cutter": {
                "key": "2",
                "tags": ["Marksman"],
                "info": {"attack": 8, "defense": 2, "magic": 2, "difficulty": 4},
                "stats": {"attackrange": 525},
                "passive": {
                    "name": "P",
                    "description": "Attacks apply Grievous Wounds, reducing healing.",
                },
                "spells": [{"name": "Stun", "description": "Stuns the target."}],
            },
        }
    }
    profiles = parse_champion_profiles(document)
    assert "HEALING" in profiles[1].traits and "HEALING" not in profiles[2].traits
    assert profiles[2].hard_cc_spells == 1 and not profiles[2].melee


def champion(
    cid: int, tags: tuple[str, ...], magic: int, attack: int, traits=()
) -> ChampionProfile:
    return ChampionProfile(cid, str(cid), tags, attack, 5, magic, 5, 150.0, frozenset(traits))


def test_situation_counts_only_what_happened_before_the_anchor():
    participants = (
        ParticipantView(1, 101, 100, "TOP"),
        ParticipantView(6, 201, 200, "TOP"),
        ParticipantView(7, 202, 200, "MIDDLE"),
    )
    timelines = [
        ParticipantTimeline(1, [ItemEvent(500, 3071, "PURCHASE", "MAIN")]),
        ParticipantTimeline(
            6, [ItemEvent(800, 3075, "PURCHASE", "MAIN"), ItemEvent(2000, 3065, "PURCHASE", "MAIN")]
        ),  # depois da âncora
        ParticipantTimeline(
            7, [ItemEvent(700, 3165, "PURCHASE", "MAIN"), ItemEvent(900, 3165, "SOLD", "MAIN")]
        ),
    ]
    kills = [Kill(600, 7, 1, (6,)), Kill(1000, 7, 1, ()), Kill(1500, 1, 6, ())]
    data = SituationInput(
        participants,
        timelines,
        kills,
        [Objective(400, "DRAGON", 200, None)],
        [GoldFrame(0, {1: 500, 6: 500, 7: 500}), GoldFrame(900, {1: 2000, 6: 2500, 7: 3000})],
    )
    items = {
        3071: ItemProfile(3071, {"ad": 45.0, "hp": 400.0}, frozenset({"ARMOR_SHRED"})),
        3075: ItemProfile(3075, {"armor": 75.0, "hp": 150.0}, frozenset({"ANTIHEAL"})),
        3165: ItemProfile(3165, {"ap": 75.0}, frozenset({"ANTIHEAL"})),
    }
    champions = {
        101: champion(101, ("Fighter",), 2, 8),
        201: champion(201, ("Tank",), 3, 5, ("HEALING",)),
        202: champion(202, ("Assassin", "Mage"), 9, 2),
    }
    features = situation_features(data, 1, 1000, items=items, champions=champions)
    assert owned_main_items(timelines[1], 1000) == [3075]
    assert features["enemy_items"] == 1.0 and features["enemy_items_armor"] == 75.0
    assert features["enemy_items_antiheal"] == 1.0  # a Morello foi vendida
    assert features["self_deaths"] == 1.0  # a morte das 1000 é no instante da âncora: fora
    assert features["deaths_to_magic"] == 1.0 and features["deaths_to_assassin"] == 1.0
    assert features["dragons_diff"] == -1.0 and features["enemy_healers"] == 1.0
    assert features["team_gold_diff"] == 2000 - 5500
    assert features["self_role"] == "TOP" and features["top_enemy_tag"] == "Assassin"
    unknown = situation_features(data, 1, 1000, items=items, champions={})
    assert math.isnan(unknown["enemy_magic_share"])
