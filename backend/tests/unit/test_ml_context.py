"""CC-408 rich context: only events and frames strictly before the anchor are used."""

from __future__ import annotations

import math

from riftlab.ml.context import rich_context

MATCH = {
    "info": {
        "participants": [
            {"participantId": 1, "teamId": 100, "teamPosition": "BOTTOM", "championId": 51},
            {"participantId": 2, "teamId": 100, "teamPosition": "UTILITY", "championId": 12},
            {"participantId": 6, "teamId": 200, "teamPosition": "BOTTOM", "championId": 145},
            {"participantId": 7, "teamId": 200, "teamPosition": "TOP", "championId": 99},
        ]
    }
}
TAGS = {3075: ("Armor", "Health"), 3031: ("CriticalStrike", "Damage")}


def _timeline(after_anchor: bool) -> dict:
    events = [
        {"type": "ITEM_PURCHASED", "timestamp": 400_000, "participantId": 7, "itemId": 3075},
        {"type": "ITEM_PURCHASED", "timestamp": 410_000, "participantId": 2, "itemId": 3031},
        {"type": "CHAMPION_KILL", "timestamp": 420_000, "killerId": 1},
        {"type": "CHAMPION_KILL", "timestamp": 430_000, "killerId": 6},
        {"type": "CHAMPION_KILL", "timestamp": 440_000, "killerId": 7},
    ]
    late = [
        {"type": "ITEM_PURCHASED", "timestamp": 700_000, "participantId": 6, "itemId": 3075},
        {"type": "CHAMPION_KILL", "timestamp": 700_000, "killerId": 6},
    ]
    frames = [
        {
            "timestamp": 480_000,
            "participantFrames": {"1": {"totalGold": 3000}, "6": {"totalGold": 2000}},
            "events": events,
        },
        {
            "timestamp": 600_000,
            "participantFrames": {"1": {"totalGold": 9000}, "6": {"totalGold": 1000}},
            "events": late if after_anchor else [],
        },
    ]
    return {"info": {"frames": frames}}


def test_context_reads_only_before_the_anchor() -> None:
    clean = rich_context(MATCH, _timeline(False), 1, 500_000, TAGS)
    with_future = rich_context(MATCH, _timeline(True), 1, 500_000, TAGS)

    assert clean == with_future
    assert clean["lane_opponent_champion_id"] == "145"
    assert clean["team_gold_diff"] == 1000.0  # last frame before the anchor
    assert clean["kill_diff"] == -1.0  # one ally kill, two enemy kills
    assert (clean["enemy_main_items"], clean["enemy_items_armor"], clean["ally_main_items"]) == (
        1.0,
        1.0,
        1.0,
    )


def test_no_frame_before_anchor_is_nan_not_zero() -> None:
    context = rich_context(MATCH, _timeline(False), 1, 1_000, TAGS)
    assert math.isnan(context["team_gold_diff"])
    assert context["enemy_main_items"] == 0.0
