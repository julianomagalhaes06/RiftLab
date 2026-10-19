"""Linha do tempo da tela de análise: desfazer, sistema, objetivos e quadros de ouro."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from riftlab.matches.timeline import RawEvent, build_timeline, parse_gold_frames
from riftlab.preparation.application import frames_document

TEAMS = {1: 100, 2: 100, 6: 200}


def _e(t: int, kind: str, **payload: Any) -> RawEvent:
    return RawEvent(t, kind, payload)


def test_undo_removes_the_last_purchase_or_sale_of_the_same_item() -> None:
    events = [
        _e(1000, "ITEM_PURCHASED", participantId=1, itemId=1055),
        _e(1100, "ITEM_PURCHASED", participantId=1, itemId=2003),
        _e(1200, "ITEM_UNDO", participantId=1, beforeId=2003, afterId=0, goldGain=50),
        _e(5000, "ITEM_SOLD", participantId=1, itemId=1055),
        _e(5100, "ITEM_UNDO", participantId=1, beforeId=0, afterId=1055, goldGain=-360),
        _e(6000, "ITEM_PURCHASED", participantId=0, itemId=3865),  # sistema
        _e(6000, "ITEM_PURCHASED", participantId=9, itemId=3865),  # fora da partida
    ]
    [one, two, six], _, _ = build_timeline(events, [1, 2, 6], TEAMS)

    assert [(i.timestamp_ms, i.item_id, i.kind) for i in one.items] == [(1000, 1055, "PURCHASE")]
    assert two.items == [] and six.items == []


def test_skills_kills_and_objectives_keep_only_valid_facts() -> None:
    events = [
        _e(70_000, "SKILL_LEVEL_UP", participantId=1, skillSlot=1, levelUpType="NORMAL"),
        _e(71_000, "SKILL_LEVEL_UP", participantId=1, skillSlot=7, levelUpType="NORMAL"),
        _e(300_000, "CHAMPION_KILL", killerId=6, victimId=1, assistingParticipantIds=[2]),
        _e(301_000, "CHAMPION_KILL", killerId=0, victimId=2),
        _e(
            600_000,
            "ELITE_MONSTER_KILL",
            killerId=6,
            killerTeamId=200,
            monsterType="DRAGON",
            monsterSubType="FIRE_DRAGON",
        ),
        _e(700_000, "ELITE_MONSTER_KILL", killerId=1, monsterType="HORDE"),
        _e(
            900_000,
            "BUILDING_KILL",
            teamId=100,
            killerId=6,
            buildingType="TOWER_BUILDING",
            towerType="OUTER_TURRET",
        ),
    ]
    [one, _, _], kills, objectives = build_timeline(events, [1, 2, 6], TEAMS)

    assert [(s.skill_slot, s.timestamp_ms) for s in one.skills] == [(1, 70_000)]
    assert [(k.killer_id, k.victim_id, k.assisting_ids) for k in kills] == [
        (6, 1, (2,)),
        (0, 2, ()),
    ]
    assert [(o.kind, o.team, o.sub_type) for o in objectives] == [
        ("DRAGON", 200, "FIRE_DRAGON"),
        ("VOIDGRUB", 100, None),  # sem killerTeamId: time do executor
        ("TOWER", 200, "OUTER_TURRET"),  # teamId é o DONO da torre
    ]


@dataclass
class _Frame:
    timestamp_ms: int
    participant_frames: dict[int, dict[str, Any]] = field(default_factory=dict)


def test_frames_keep_only_valid_values_and_round_trip() -> None:
    document = frames_document(
        [
            _Frame(0, {1: {"totalGold": 500}, 2: {"totalGold": True}, 6: {}}),
            _Frame(
                60_000,
                {
                    1: {"totalGold": 820, "xp": 300, "minionsKilled": 7, "jungleMinionsKilled": 1},
                    6: {"totalGold": 900, "xp": -1, "minionsKilled": 5, "level": 2},
                },
            ),
        ]
    )
    # CC-1003: farm = tropas + selva (só com os dois válidos); valor inválido fica de fora.
    assert document == [
        {"t": 0, "gold": {"1": 500}, "xp": {}, "cs": {}, "level": {}},
        {
            "t": 60_000,
            "gold": {"1": 820, "6": 900},
            "xp": {"1": 300},
            "cs": {"1": 8},
            "level": {"6": 2},
        },
    ]
    frames = parse_gold_frames(document + [{"t": "x"}, "lixo"])
    assert [(f.timestamp_ms, dict(f.gold)) for f in frames] == [
        (0, {1: 500}),
        (60_000, {1: 820, 6: 900}),
    ]


async def test_missing_raw_never_fails_the_preparation_only_skips_frames(monkeypatch) -> None:
    from contextlib import asynccontextmanager

    import riftlab.preparation.application as preparation
    from riftlab.shared.errors import SourceNotFoundError

    added: list[Any] = []

    class _Session:
        async def get(self, *_: Any) -> None:
            return None

        def add(self, row: Any) -> None:
            added.append(row)

    @asynccontextmanager
    async def fake_uow(_: Any):  # type: ignore[no-untyped-def]
        yield _Session()

    class _Source:
        async def load_match(self, match_id: str) -> Any:
            raise SourceNotFoundError(code="RAW_MATCH_NOT_CACHED", detail="sem bruto")

    monkeypatch.setattr(preparation, "unit_of_work", fake_uow)
    await preparation._ensure_frames(None, _Source(), "BR1_1")  # type: ignore[arg-type]
    assert added == []  # nada gravado e nenhuma exceção: a preparação segue
