"""Rota estimada na partida ao vivo (CC-1214). A Spectator-V5 não informa a rota de ninguém, só
os dez jogadores na ordem dela; sites como o op.gg estimam. Aqui a estimativa usa:

- quantas vezes cada campeão jogou em cada rota na referência da revisão da partida
  (`participations` de `configs/match-review/reference-*.json`, partidas Solo/Duo reais);
- os feitiços: Golpear é quase sempre da selva; Teleporte puxa para o topo, Curar para o atirador
  e Exaustão para o suporte.

Para cada time de cinco escolhe, entre as 120 combinações, a de maior probabilidade conjunta.
É uma estimativa e é marcada como tal na resposta e na tela.
"""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from functools import lru_cache
from itertools import permutations
from pathlib import Path

from riftlab.shared.numeric import is_ascii_digits

ROLES = ("TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY")
SMITE, TELEPORT, HEAL, EXHAUST, BARRIER = 11, 12, 7, 3, 21
# Fatores multiplicados à frequência do campeão na rota (1 quando ausente).
_SPELL_WEIGHTS: dict[int, dict[str, float]] = {
    SMITE: {"TOP": 0.001, "JUNGLE": 50.0, "MIDDLE": 0.001, "BOTTOM": 0.001, "UTILITY": 0.001},
    TELEPORT: {"TOP": 2.0, "MIDDLE": 1.2, "JUNGLE": 0.5},
    HEAL: {"BOTTOM": 2.0, "UTILITY": 1.3},
    BARRIER: {"BOTTOM": 1.3, "MIDDLE": 1.3},
    EXHAUST: {"UTILITY": 2.0, "MIDDLE": 0.8},
}


@lru_cache(maxsize=4)
def champion_roles(path: str) -> dict[int, dict[str, int]]:
    """{campeão: {rota: partidas}} da referência; {} sem arquivo ou fora do formato."""
    file = Path(path)
    if not file.exists():
        return {}
    try:
        document = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    raw = document.get("participations") if isinstance(document, dict) else None
    roles: dict[int, dict[str, int]] = {}
    for key, count in raw.items() if isinstance(raw, dict) else ():
        champion, _, role = str(key).partition(":")
        if is_ascii_digits(champion) and role in ROLES and isinstance(count, int) and count > 0:
            roles.setdefault(int(champion), {})[role] = count
    return roles


# Com Golpear no time, quem não o leva quase nunca é o caçador.
_JUNGLE_WITHOUT_SMITE = 0.01


def _score(
    champion_id: int,
    spells: Sequence[int | None],
    role: str,
    known: dict[int, dict[str, int]],
    team_has_smite: bool,
) -> float:
    counts = known.get(champion_id, {})
    total = sum(counts.values())
    probability = (counts.get(role, 0) + 1) / (total + len(ROLES))  # suavização de Laplace
    for spell in spells:
        if spell is not None:
            probability *= _SPELL_WEIGHTS.get(spell, {}).get(role, 1.0)
    if role == "JUNGLE" and team_has_smite and SMITE not in spells:
        probability *= _JUNGLE_WITHOUT_SMITE
    return math.log(probability)


def assign_positions(
    players: Sequence[tuple[int, Sequence[int | None]]], known: dict[int, dict[str, int]]
) -> list[str] | None:
    """Rota de cada um dos cinco jogadores (campeão, feitiços), na mesma ordem; None quando o
    time não tem exatamente cinco jogadores (outros modos de jogo)."""
    if len(players) != len(ROLES):
        return None
    smite = any(SMITE in spells for _, spells in players)
    scores = [[_score(c, spells, role, known, smite) for role in ROLES] for c, spells in players]
    best = max(
        permutations(range(len(ROLES))),
        key=lambda order: sum(scores[i][order[i]] for i in range(len(ROLES))),
    )
    return [ROLES[best[i]] for i in range(len(ROLES))]
