"""CC-408: rich pre-anchor context from the raw match and timeline.

Only events and frames strictly before the anchor are read: the lane opponent's
champion (enemy with the same team position), main items completed by enemies
and allies by tag, team gold difference at the last frame before the anchor, and
kill difference. Everything else in the raw bodies is ignored.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

CONTEXT_ITEM_TAGS = (
    "Armor",
    "SpellBlock",
    "Health",
    "CriticalStrike",
    "AttackSpeed",
    "SpellDamage",
    "Damage",
    "LifeSteal",
)
RICH_CONTEXT = (
    "lane_opponent_champion_id",
    "team_gold_diff",
    "kill_diff",
    "enemy_main_items",
    *(f"enemy_items_{tag.lower()}" for tag in CONTEXT_ITEM_TAGS),
    "ally_main_items",
)


def rich_context(
    match: Mapping[str, Any],
    timeline: Mapping[str, Any],
    participant_id: int,
    anchor_ms: int,
    main_item_tags: Mapping[int, Sequence[str]],
) -> dict[str, Any]:
    """Features for one participant at one anchor; NaN where nothing is observable."""
    participants = {int(p["participantId"]): p for p in match["info"]["participants"]}
    me = participants[participant_id]
    team = me["teamId"]
    enemies = {pid for pid, p in participants.items() if p["teamId"] != team}
    allies = {
        pid for pid, p in participants.items() if p["teamId"] == team and pid != participant_id
    }
    opponent = next(
        (
            participants[pid]["championId"]
            for pid in sorted(enemies)
            if participants[pid].get("teamPosition") == me.get("teamPosition")
        ),
        None,
    )
    enemy_tags: Counter[str] = Counter()
    enemy_items = ally_items = 0
    kills: Counter[str] = Counter()
    gold_diff = math.nan
    for frame in timeline["info"]["frames"]:
        if frame.get("timestamp", 0) < anchor_ms:
            frames = frame.get("participantFrames", {})
            own = sum(
                int(v.get("totalGold", 0)) for k, v in frames.items() if int(k) not in enemies
            )
            other = sum(int(v.get("totalGold", 0)) for k, v in frames.items() if int(k) in enemies)
            gold_diff = float(own - other) if frames else math.nan
        for event in frame.get("events", []):
            if event.get("timestamp", 0) >= anchor_ms:
                continue
            kind = event.get("type")
            if kind == "CHAMPION_KILL":
                killer = event.get("killerId")
                if killer in enemies:
                    kills["enemy"] += 1
                elif isinstance(killer, int) and killer in participants:
                    kills["ally"] += 1
            elif kind == "ITEM_PURCHASED" and event.get("itemId") in main_item_tags:
                buyer = event.get("participantId")
                if buyer in enemies:
                    enemy_items += 1
                    enemy_tags.update(main_item_tags[event["itemId"]])
                elif buyer in allies:
                    ally_items += 1
    return {
        "lane_opponent_champion_id": str(opponent) if opponent is not None else "NONE",
        "team_gold_diff": gold_diff,
        "kill_diff": float(kills["ally"] - kills["enemy"]),
        "enemy_main_items": float(enemy_items),
        **{f"enemy_items_{tag.lower()}": float(enemy_tags[tag]) for tag in CONTEXT_ITEM_TAGS},
        "ally_main_items": float(ally_items),
    }
