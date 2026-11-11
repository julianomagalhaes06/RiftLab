"""Gamificação do estudo: XP por decisão (melhor tentativa), nível, sequência em horário de
Brasília, conquistas, missões da semana e progresso da partida, só de tentativas reais."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from riftlab.learning.gamification import AttemptFact, level_for, summarize

NOW = datetime(2026, 11, 11, 15, 0, tzinfo=UTC)  # quarta-feira, 12h em Brasília


def fact(
    decision: uuid.UUID,
    *,
    days_ago: int = 0,
    status: str = "REVEALED",
    hint: int = 0,
    reflected: bool = False,
    lost: bool = False,
    skipped: bool = False,
    match: str = "BR1_1",
    participant: int = 1,
) -> AttemptFact:
    return AttemptFact(
        decision_id=decision,
        match_id=match,
        participant_id=participant,
        created_at=NOW - timedelta(days=days_ago),
        status=status,
        skipped=skipped,
        hint_level=hint,
        reflected=reflected,
        lost=lost,
    )


def test_level_curve() -> None:
    assert level_for(0) == (1, 0, 100)
    assert level_for(99) == (1, 99, 100)
    assert level_for(100) == (2, 0, 150)
    assert level_for(260) == (3, 10, 200)


def test_xp_counts_the_best_attempt_per_decision_and_ignores_open_or_skipped() -> None:
    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    facts = [
        fact(a, status="ANSWERED"),  # 20
        fact(a, status="REVEALED", reflected=True),  # 55 (vence a de 20)
        fact(b, status="REVEALED", hint=1),  # 30 (revelou com dica)
        fact(c, status="OPEN"),  # 0
        fact(c, status="ANSWERED", skipped=True),  # 0
    ]
    game = summarize(facts, now=NOW, eligible_by_match={})
    assert game["xp"] == 85 and game["studiedDecisions"] == 2
    unlocked = {a["key"] for a in game["achievements"] if a["unlocked"]}
    assert {"first_study", "first_reflection"} <= unlocked and "no_hint_5" not in unlocked


def test_streak_survives_until_the_end_of_the_next_day() -> None:
    days = [uuid.uuid4() for _ in range(5)]
    facts = [
        fact(days[0], days_ago=1),
        fact(days[1], days_ago=2),
        fact(days[2], days_ago=3),
        fact(days[3], days_ago=6),
        fact(days[4], days_ago=7),
    ]
    streak = summarize(facts, now=NOW, eligible_by_match={})["streak"]
    assert streak == {"current": 3, "best": 3, "studiedToday": False}
    streak = summarize([*facts, fact(uuid.uuid4())], now=NOW, eligible_by_match={})["streak"]
    assert streak == {"current": 4, "best": 4, "studiedToday": True}
    stale = summarize([fact(uuid.uuid4(), days_ago=2)], now=NOW, eligible_by_match={})["streak"]
    assert stale["current"] == 0 and stale["best"] == 1


def test_full_match_loss_missions_and_match_progress() -> None:
    a, b, other = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    facts = [fact(a, lost=True), fact(b, reflected=True), fact(other, match="BR1_2", days_ago=9)]
    game = summarize(facts, now=NOW, eligible_by_match={("BR1_1", 1): {a, b}}, match_id="BR1_1")
    unlocked = {x["key"] for x in game["achievements"] if x["unlocked"]}
    assert {"full_match", "learn_from_loss"} <= unlocked
    assert game["weekStart"] == "2026-11-09"  # segunda-feira
    assert len({m["key"] for m in game["missions"]}) == 3
    # Missões só contam a semana: a decisão de 9 dias atrás fica de fora.
    progress = {m["key"]: m["progress"] for m in game["missions"]}
    if "matches_2" in progress:
        assert progress["matches_2"] == 1
    assert game["match"]["studiedDecisionIds"] == sorted([str(a), str(b)])
    assert game["match"]["xp"] == 40 + 55
