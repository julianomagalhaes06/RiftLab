"""Chance de vitória por minuto (CC-1004): variáveis e modelo logístico.

As mesmas funções servem ao treino offline e à tela. Só quadros por minuto e eventos de
abate, estrutura e objetivo até o minuto (nada de campeão, jogador ou rota). Perspectiva do
time azul (100); a do vermelho é o complemento.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

BLUE, RED = 100, 200
FEATURE_NAMES = (
    "intercept",
    "minute",
    "gold",
    "xp",
    "kills",
    "towers",
    "inhibitors",
    "dragons",
    "elder",
    "barons",
    "heralds",
    "grubs",
    "atakhan",
    "goldXMinute",
    "towersXMinute",
)
_OBJECTIVE_FEATURE = {
    "TOWER": "towers",
    "INHIBITOR": "inhibitors",
    "BARON": "barons",
    "HERALD": "heralds",
    "VOIDGRUB": "grubs",
    "ATAKHAN": "atakhan",
}


@dataclass(frozen=True, slots=True)
class StatFrame:
    """Totais por participante num quadro (ouro total e XP)."""

    timestamp_ms: int
    gold: Mapping[int, int]
    xp: Mapping[int, int]


@dataclass(frozen=True, slots=True)
class TeamEvent:
    """Abate ou objetivo creditado a um time (100 ou 200)."""

    timestamp_ms: int
    feature: str
    team: int


@dataclass(frozen=True, slots=True)
class MinuteFeatures:
    timestamp_ms: int
    values: tuple[float, ...]


def team_events(
    kills: Sequence[Any], objectives: Sequence[Any], teams: Mapping[int, int]
) -> list[TeamEvent]:
    """`kills`/`objectives` no formato de `matches.timeline.build_timeline` (abate sem
    assassino participante, ex. execução, vai para o time adversário da vítima)."""
    out: list[TeamEvent] = []
    for kill in kills:
        team = teams.get(kill.killer_id)
        if team is None:
            victim = teams.get(kill.victim_id)
            team = {BLUE: RED, RED: BLUE}.get(victim) if victim is not None else None
        if team in (BLUE, RED):
            out.append(TeamEvent(kill.timestamp_ms, "kills", team))
    for objective in objectives:
        if objective.team not in (BLUE, RED):
            continue
        if objective.kind == "DRAGON":
            feature = "elder" if objective.sub_type == "ELDER_DRAGON" else "dragons"
        else:
            feature = _OBJECTIVE_FEATURE.get(objective.kind, "")
        if feature:
            out.append(TeamEvent(objective.timestamp_ms, feature, objective.team))
    return sorted(out, key=lambda event: event.timestamp_ms)


def minute_features(
    frames: Sequence[StatFrame], teams: Mapping[int, int], events: Sequence[TeamEvent]
) -> list[MinuteFeatures]:
    """Uma linha por quadro com t ≥ 60 s; eventos contam até o instante do quadro."""
    rows: list[MinuteFeatures] = []
    for frame in frames:
        if frame.timestamp_ms < 60_000:
            continue
        diff = {name: 0.0 for name in _OBJECTIVE_FEATURE.values()} | {
            "kills": 0.0,
            "dragons": 0.0,
            "elder": 0.0,
        }
        for event in events:
            if event.timestamp_ms > frame.timestamp_ms:
                break
            diff[event.feature] += 1.0 if event.team == BLUE else -1.0
        gold = sum(v for pid, v in frame.gold.items() if teams.get(pid) == BLUE) - sum(
            v for pid, v in frame.gold.items() if teams.get(pid) == RED
        )
        xp = sum(v for pid, v in frame.xp.items() if teams.get(pid) == BLUE) - sum(
            v for pid, v in frame.xp.items() if teams.get(pid) == RED
        )
        minute = frame.timestamp_ms / 60_000 / 30
        gold_k = gold / 1000
        rows.append(
            MinuteFeatures(
                frame.timestamp_ms,
                (
                    1.0,
                    minute,
                    gold_k,
                    xp / 1000,
                    diff["kills"],
                    diff["towers"],
                    diff["inhibitors"],
                    diff["dragons"],
                    diff["elder"],
                    diff["barons"],
                    diff["heralds"],
                    diff["grubs"],
                    diff["atakhan"],
                    gold_k * minute,
                    diff["towers"] * minute,
                ),
            )
        )
    return rows


@dataclass(frozen=True, slots=True)
class WinProbabilityModel:
    model_version: str
    coefficients: tuple[float, ...]
    gate_passed: bool

    def blue_probability(self, features: MinuteFeatures) -> float:
        z = sum(w * x for w, x in zip(self.coefficients, features.values, strict=True))
        return 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, z))))


def load_model(path: Path) -> WinProbabilityModel | None:
    """Modelo versionado (coeficientes + resultado do gate). Ausente ⇒ None."""
    if not path.exists():
        return None
    document = json.loads(path.read_text(encoding="utf-8"))
    if tuple(document.get("featureNames", ())) != FEATURE_NAMES:
        raise ValueError("modelo de chance de vitória com variáveis divergentes")
    coefficients = tuple(float(value) for value in document["coefficients"])
    return WinProbabilityModel(
        str(document["modelVersion"]), coefficients, bool(document["gate"]["passed"])
    )
