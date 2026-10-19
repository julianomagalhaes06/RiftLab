"""Fase de rotas (CC-1003): ouro, XP e farm aos 10 e 15 minutos contra o oponente direto
(mesma rota, outro time), a partir dos quadros gravados na preparação."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from riftlab.match_review.win_probability import StatFrame
from riftlab.shared.numeric import is_ascii_digits

LANE_MINUTES = (10, 15)
_FIELDS = ("gold", "xp", "cs", "level")


@dataclass(frozen=True, slots=True)
class FrameStats:
    timestamp_ms: int
    values: Mapping[str, Mapping[int, int]]

    def stat_frame(self) -> StatFrame:
        return StatFrame(self.timestamp_ms, self.values.get("gold", {}), self.values.get("xp", {}))


@dataclass(frozen=True, slots=True)
class LaneComparison:
    minute: int
    own: Mapping[str, int]
    opponent: Mapping[str, int]


def parse_frames(document: Any) -> list[FrameStats]:
    """Documento de `match_frames` (CC-1003: ouro, XP, farm e nível; antes só ouro)."""
    frames: list[FrameStats] = []
    for frame in document if isinstance(document, list) else ():
        if not isinstance(frame, dict) or not isinstance(frame.get("t"), int):
            continue
        values: dict[str, dict[int, int]] = {}
        for name in _FIELDS:
            raw = frame.get(name)
            if isinstance(raw, dict):
                values[name] = {
                    int(pid): value
                    for pid, value in raw.items()
                    if is_ascii_digits(str(pid))
                    and isinstance(value, int)
                    and not isinstance(value, bool)
                }
        frames.append(FrameStats(frame["t"], values))
    return frames


def has_lane_stats(frames: Sequence[FrameStats]) -> bool:
    return any(frame.values.get("xp") for frame in frames)


def direct_opponent(
    participant_id: int, roles: Mapping[int, str | None], teams: Mapping[int, int]
) -> int | None:
    role = roles.get(participant_id)
    if role is None:
        return None
    rivals = [
        pid
        for pid, other in roles.items()
        if other == role and teams.get(pid) not in (None, teams.get(participant_id))
    ]
    return rivals[0] if len(rivals) == 1 else None


def lane_comparison(
    frames: Sequence[FrameStats], participant_id: int, opponent_id: int
) -> list[LaneComparison]:
    """Primeiro quadro em ou depois de cada minuto; minuto sem quadro (partida curta) fica fora."""
    out: list[LaneComparison] = []
    for minute in LANE_MINUTES:
        frame = next((f for f in frames if f.timestamp_ms >= minute * 60_000), None)
        if frame is None:
            continue
        own = {name: frame.values.get(name, {}).get(participant_id) for name in _FIELDS}
        rival = {name: frame.values.get(name, {}).get(opponent_id) for name in _FIELDS}
        if any(value is None for value in (*own.values(), *rival.values())):
            continue
        out.append(
            LaneComparison(
                minute,
                {k: int(v) for k, v in own.items() if v is not None},
                {k: int(v) for k, v in rival.items() if v is not None},
            )
        )
    return out
