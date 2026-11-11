"""Detalhes de placar dos participantes (feitiços, runas, nível, dano, ouro, visão), lidos na
hora do corpo bruto da Riot que o worker guardou, como o Riot ID (CC-1210). Nada é gravado no
banco: expurgar a partida apaga o bruto e, com ele, estes detalhes. Valores fora do tipo ou da
faixa esperada viram `None` (a interface mostra "–")."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from riftlab.matches.riot_ids import _raw_participants

_MAX_STAT = 1_000_000
_MAX_ID = 100_000


@dataclass(frozen=True)
class ParticipantRunesRaw:
    primary_style: int
    keystone: int
    secondary_style: int | None


@dataclass(frozen=True)
class ParticipantDetailsRaw:
    summoner_spells: tuple[int, int] | None
    runes: ParticipantRunesRaw | None
    champ_level: int | None
    damage_to_champions: int | None
    gold_earned: int | None
    wards_placed: int | None
    vision_score: int | None
    largest_multi_kill: int | None


def _int(value: Any, maximum: int = _MAX_STAT) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if 0 <= value <= maximum else None


def _id(value: Any) -> int | None:
    number = _int(value, _MAX_ID)
    return number if number else None


def _runes(perks: Any) -> ParticipantRunesRaw | None:
    styles = perks.get("styles") if isinstance(perks, dict) else None
    if not isinstance(styles, list):
        return None
    by_kind = {s.get("description"): s for s in styles if isinstance(s, dict)}
    primary = by_kind.get("primaryStyle")
    secondary = by_kind.get("subStyle")
    if primary is None:
        return None
    selections = primary.get("selections")
    first = selections[0] if isinstance(selections, list) and selections else None
    primary_style = _id(primary.get("style"))
    keystone = _id(first.get("perk")) if isinstance(first, dict) else None
    if primary_style is None or keystone is None:
        return None
    return ParticipantRunesRaw(
        primary_style=primary_style,
        keystone=keystone,
        secondary_style=_id(secondary.get("style")) if isinstance(secondary, dict) else None,
    )


def _details(raw: dict[str, Any]) -> ParticipantDetailsRaw:
    spell1, spell2 = _id(raw.get("summoner1Id")), _id(raw.get("summoner2Id"))
    return ParticipantDetailsRaw(
        summoner_spells=(spell1, spell2) if spell1 and spell2 else None,
        runes=_runes(raw.get("perks")),
        champ_level=_int(raw.get("champLevel"), 30),
        damage_to_champions=_int(raw.get("totalDamageDealtToChampions")),
        gold_earned=_int(raw.get("goldEarned")),
        wards_placed=_int(raw.get("wardsPlaced"), 1000),
        vision_score=_int(raw.get("visionScore"), 1000),
        largest_multi_kill=_int(raw.get("largestMultiKill"), 5),
    )


def participant_details(raw_dir: str, match_id: str) -> dict[int, ParticipantDetailsRaw]:
    """participantId → detalhes do placar. {} para DEMO, bruto ausente ou corrompido."""
    found: dict[int, ParticipantDetailsRaw] = {}
    for raw in _raw_participants(raw_dir, match_id):
        pid = raw.get("participantId")
        if isinstance(pid, int) and not isinstance(pid, bool) and 1 <= pid <= 10:
            found[pid] = _details(raw)
    return found
