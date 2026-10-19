"""Fase de rotas e chance de vitória (CC-1003/CC-1004): funções puras compartilhadas."""

from __future__ import annotations

from riftlab.match_review.application import key_moments
from riftlab.match_review.lane import direct_opponent, lane_comparison, parse_frames
from riftlab.match_review.win_probability import (
    FEATURE_NAMES,
    MinuteFeatures,
    StatFrame,
    WinProbabilityModel,
    minute_features,
    team_events,
)
from riftlab.matches.timeline import Kill, Objective

TEAMS = {1: 100, 2: 100, 6: 200, 7: 200}


def test_frames_parse_old_and_new_documents() -> None:
    old = parse_frames([{"t": 60_000, "gold": {"1": 500}}])
    assert old[0].values == {"gold": {1: 500}}
    new = parse_frames(
        [
            {
                "t": 600_000,
                "gold": {"1": 3000, "6": 2500},
                "xp": {"1": 4000, "6": 3500},
                "cs": {"1": 80, "6": 70},
                "level": {"1": 8, "6": 7},
            }
        ]
    )
    assert new[0].values["cs"] == {1: 80, 6: 70}


def test_direct_opponent_needs_exactly_one_rival_in_the_role() -> None:
    roles = {1: "TOP", 2: "JUNGLE", 6: "TOP", 7: "JUNGLE"}
    assert direct_opponent(1, roles, TEAMS) == 6
    assert direct_opponent(1, {**roles, 7: "TOP"}, TEAMS) is None
    assert direct_opponent(1, {**roles, 1: None}, TEAMS) is None


def test_lane_comparison_uses_the_first_frame_at_or_after_each_minute() -> None:
    frames = parse_frames(
        [
            {
                "t": 540_000,
                "gold": {"1": 1, "6": 1},
                "xp": {"1": 1, "6": 1},
                "cs": {"1": 1, "6": 1},
                "level": {"1": 1, "6": 1},
            },
            {
                "t": 600_400,
                "gold": {"1": 3000, "6": 2500},
                "xp": {"1": 4000, "6": 3500},
                "cs": {"1": 80, "6": 70},
                "level": {"1": 8, "6": 7},
            },
        ]
    )
    [ten] = lane_comparison(frames, 1, 6)
    assert ten.minute == 10 and ten.own["gold"] == 3000 and ten.opponent["cs"] == 70


def test_minute_features_count_events_up_to_the_frame() -> None:
    kills = [Kill(70_000, 1, 6, ()), Kill(130_000, 0, 1, ())]  # execução conta para o adversário
    objectives = [
        Objective(100_000, "DRAGON", 200, "FIRE_DRAGON"),
        Objective(110_000, "TOWER", 100, "OUTER_TURRET"),
        Objective(120_000, "DRAGON", 100, "ELDER_DRAGON"),
    ]
    frames = [
        StatFrame(60_000, {1: 500, 6: 400}, {1: 300, 6: 300}),
        StatFrame(120_000, {1: 900, 6: 1100}, {1: 600, 6: 700}),
    ]
    rows = minute_features(frames, TEAMS, team_events(kills, objectives, TEAMS))
    first, second = (dict(zip(FEATURE_NAMES, r.values, strict=True)) for r in rows)
    assert first["kills"] == 0 and first["gold"] == 0.1
    assert (
        second["kills"] == 1
        and second["dragons"] == -1
        and second["towers"] == 1
        and second["elder"] == 1
    )
    assert second["gold"] == -0.2 and abs(second["goldXMinute"] - (-0.2 * 2 / 30)) < 1e-12


def test_model_probability_and_key_moments() -> None:
    model = WinProbabilityModel("m1", (0.0, 0, 1.0, *([0.0] * 12)), True)
    row = MinuteFeatures(60_000, (1.0, 0, 0.0, *([0.0] * 12)))
    assert model.blue_probability(row) == 0.5
    series = [(60_000, 0.5), (120_000, 0.52), (180_000, 0.30), (240_000, 0.33), (300_000, 0.60)]
    kills = [Kill(150_000, 6, 1, (7,)), Kill(250_000, 1, 6, ())]
    moments = key_moments(series, kills, [Objective(260_000, "BARON", 100, None)], 1)
    assert [m.minute for m in moments] == [3, 5]
    assert moments[0].events[0].kind == "DEATH" and moments[0].events[0].involves_participant
    assert {e.kind for e in moments[1].events} == {"KILL", "BARON"}
