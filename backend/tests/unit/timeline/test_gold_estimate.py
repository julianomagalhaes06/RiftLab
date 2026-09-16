"""select_gold_estimate (CC-203): seleção de frame estritamente anterior ao
evento e classificação de idade/qualidade. Puro, sem I/O."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from riftlab.timeline.application import STALE_FRAME_THRESHOLD_MS, select_gold_estimate

P1 = 1


@dataclass(frozen=True, slots=True)
class _Frame:
    timestamp_ms: int
    participant_frames: dict[int, dict[str, Any]] = field(default_factory=dict)


def test_no_frame_before_event_is_unknown_not_zero() -> None:
    frames = [_Frame(1000, {P1: {"currentGold": 500}})]
    estimate = select_gold_estimate(
        frames, P1, event_timestamp_ms=1000
    )  # mesmo timestamp: não é anterior
    assert estimate.origin == "UNKNOWN"
    assert estimate.value is None
    assert estimate.age_ms is None


def test_same_timestamp_frame_never_used() -> None:
    frames = [_Frame(0, {P1: {"currentGold": 500}}), _Frame(2000, {P1: {"currentGold": 900}})]
    estimate = select_gold_estimate(frames, P1, event_timestamp_ms=2000)
    assert estimate.value == 500  # usa o frame de ts=0, nunca o de ts=2000 (mesmo instante)


def test_future_frame_never_used() -> None:
    frames = [_Frame(5000, {P1: {"currentGold": 900}})]
    estimate = select_gold_estimate(frames, P1, event_timestamp_ms=1000)
    assert estimate.origin == "UNKNOWN"
    assert estimate.value is None


def test_picks_last_frame_strictly_before_never_interpolates() -> None:
    frames = [
        _Frame(0, {P1: {"currentGold": 500}}),
        _Frame(60_000, {P1: {"currentGold": 100}}),
        _Frame(120_000, {P1: {"currentGold": 900}}),  # posterior ao evento, nunca usado
    ]
    estimate = select_gold_estimate(frames, P1, event_timestamp_ms=90_000)
    assert estimate.value == 100
    assert estimate.age_ms == 30_000
    assert estimate.origin == "OBSERVED"


def test_frame_present_without_participant_data_is_unknown() -> None:
    frames = [
        _Frame(0, {}),
        _Frame(60_000, {}),
    ]  # frame existe mas vazio (ex.: falha real de coleta)
    estimate = select_gold_estimate(frames, P1, event_timestamp_ms=61_000)
    assert estimate.origin == "UNKNOWN"
    assert estimate.value is None
    assert estimate.age_ms == 1000  # idade ainda é conhecida (o frame existe), só o dado que falta


def test_age_exactly_at_threshold_is_not_stale() -> None:
    frames = [_Frame(0, {P1: {"currentGold": 500}})]
    estimate = select_gold_estimate(frames, P1, event_timestamp_ms=STALE_FRAME_THRESHOLD_MS)
    assert estimate.age_ms == STALE_FRAME_THRESHOLD_MS
    assert estimate.stale is False


def test_age_one_ms_past_threshold_is_stale() -> None:
    frames = [_Frame(0, {P1: {"currentGold": 500}})]
    estimate = select_gold_estimate(frames, P1, event_timestamp_ms=STALE_FRAME_THRESHOLD_MS + 1)
    assert estimate.stale is True


def test_zero_gold_is_real_value_distinct_from_absent() -> None:
    frames = [_Frame(0, {P1: {"currentGold": 0}})]
    estimate = select_gold_estimate(frames, P1, event_timestamp_ms=1000)
    assert estimate.value == 0
    assert estimate.origin == "OBSERVED"


def test_non_int_gold_field_is_unknown_never_guessed() -> None:
    frames = [_Frame(0, {P1: {"currentGold": "N/A"}})]
    estimate = select_gold_estimate(frames, P1, event_timestamp_ms=1000)
    assert estimate.origin == "UNKNOWN"
    assert estimate.value is None
