"""Contexto situacional de uma decisão de late game (CC-1502). Puro.

Só usa o que aconteceu ANTES da âncora e o que o jogador vê no jogo (placar do TAB, itens dos
inimigos, quem o matou, objetivos, ouro do time). As mesmas estruturas existem no produto
(`matches.timeline.get_match_timeline`) e no corpus bruto (via `build_timeline`), então treino e
inferência passam pela mesma função.

Itens dos outros participantes: compras de item principal menos vendas e desfeitas, contadas até a
âncora (o TAB mostra os itens completos). Componentes não entram: o jogo mostra, mas o que decide
um 5º item é o que o inimigo já completou.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from riftlab.decisions.domain import ParticipantView
from riftlab.matches.timeline import GoldFrame, Kill, Objective, ParticipantTimeline
from riftlab.profiles.domain import ChampionProfile, ItemProfile

SITUATION_VERSION = "situation_v1"
CHAMPION_TAGS = ("Assassin", "Fighter", "Mage", "Marksman", "Support", "Tank")
NAN = math.nan
MAIN = "MAIN"
BOOTS = "BOOTS"

# Atributos somados dos itens completos de um lado (inimigos ou aliados).
SIDE_ITEM_STATS = ("ad", "ap", "armor", "mr", "hp", "crit", "attack_speed", "lethality")
SIDE_MECHANICS = ("ANTIHEAL", "STASIS", "SPELL_SHIELD", "REVIVE", "CLEANSE", "SHIELD")


@dataclass(frozen=True, slots=True)
class SituationInput:
    participants: tuple[ParticipantView, ...]
    timelines: Sequence[ParticipantTimeline]
    kills: Sequence[Kill]
    objectives: Sequence[Objective]
    gold_frames: Sequence[GoldFrame] | None


def owned_main_items(
    timeline: ParticipantTimeline, anchor_ms: int, *, include_same_instant: bool = False
) -> list[int]:
    """Itens principais completos (multiconjunto) até a âncora: compras menos vendas.
    `build_timeline` já aplicou os ITEM_UNDO."""
    owned: Counter[int] = Counter()
    for event in timeline.items:
        if event.timestamp_ms > anchor_ms or (
            event.timestamp_ms == anchor_ms and not include_same_instant
        ):
            break
        if event.item_class != MAIN:
            continue
        if event.kind == "PURCHASE":
            owned[event.item_id] += 1
        elif event.kind == "SOLD" and owned[event.item_id] > 0:
            owned[event.item_id] -= 1
    return sorted(owned.elements())


def _boots(timeline: ParticipantTimeline, anchor_ms: int) -> int | None:
    current: int | None = None
    for event in timeline.items:
        if event.timestamp_ms >= anchor_ms:
            break
        if event.item_class == BOOTS:
            if event.kind == "PURCHASE":
                current = event.item_id
            elif event.kind == "SOLD" and current == event.item_id:
                current = None
    return current


def _side_items(
    ids: Sequence[int], items: Mapping[int, ItemProfile], prefix: str
) -> dict[str, float]:
    out = {f"{prefix}_items": float(len(ids))}
    for stat in SIDE_ITEM_STATS:
        out[f"{prefix}_items_{stat}"] = sum(items[i].stat(stat) for i in ids if i in items)
    for mechanic in SIDE_MECHANICS:
        out[f"{prefix}_items_{mechanic.lower()}"] = float(
            sum(mechanic in items[i].mechanics for i in ids if i in items)
        )
    out[f"{prefix}_items_sustain"] = sum(
        items[i].stat("lifesteal") + items[i].stat("omnivamp") for i in ids if i in items
    )
    ad, ap = out[f"{prefix}_items_ad"], out[f"{prefix}_items_ap"]
    out[f"{prefix}_items_ap_share"] = ap / (ad + ap) if ad + ap else NAN
    return out


def _side_champions(
    ids: Sequence[int], champions: Mapping[int, ChampionProfile], prefix: str
) -> dict[str, float]:
    known = [champions[c] for c in ids if c in champions]
    if len(known) != len(ids):
        # Campeão sem perfil (novo no patch): o lado inteiro fica ausente, nunca zero.
        names = [f"{prefix}_{t.lower()}" for t in CHAMPION_TAGS] + [
            f"{prefix}_magic",
            f"{prefix}_attack",
            f"{prefix}_defense",
            f"{prefix}_magic_share",
            f"{prefix}_healers",
            f"{prefix}_shielders",
            f"{prefix}_hard_cc",
            f"{prefix}_melee",
            f"{prefix}_stealth",
        ]
        return dict.fromkeys(names, NAN)
    out: dict[str, float] = {}
    for tag in CHAMPION_TAGS:
        out[f"{prefix}_{tag.lower()}"] = float(sum(tag in c.tags for c in known))
    magic = float(sum(c.magic for c in known))
    attack = float(sum(c.attack for c in known))
    out[f"{prefix}_magic"] = magic
    out[f"{prefix}_attack"] = attack
    out[f"{prefix}_defense"] = float(sum(c.defense for c in known))
    out[f"{prefix}_magic_share"] = magic / (magic + attack) if magic + attack else NAN
    out[f"{prefix}_healers"] = float(sum("HEALING" in c.traits for c in known))
    out[f"{prefix}_shielders"] = float(sum("SHIELDING" in c.traits for c in known))
    out[f"{prefix}_hard_cc"] = float(sum(c.hard_cc_spells for c in known))
    out[f"{prefix}_melee"] = float(sum(c.melee for c in known))
    out[f"{prefix}_stealth"] = float(sum("STEALTH" in c.traits for c in known))
    return out


def _gold_at(frames: Sequence[GoldFrame] | None, anchor_ms: int) -> Mapping[int, int] | None:
    last: Mapping[int, int] | None = None
    for frame in frames or ():
        if frame.timestamp_ms >= anchor_ms:
            break
        last = frame.gold
    return last


def situation_features(
    data: SituationInput,
    participant_id: int,
    anchor_ms: int,
    *,
    items: Mapping[int, ItemProfile],
    champions: Mapping[int, ChampionProfile],
) -> dict[str, Any]:
    """Features de contexto (números ou NaN; categóricas como texto) de um participante na
    âncora. Nada no instante da âncora ou depois dela entra."""
    by_id = {p.participant_id: p for p in data.participants}
    me = by_id[participant_id]
    enemies = [p for p in data.participants if p.team != me.team]
    allies = [
        p for p in data.participants if p.team == me.team and p.participant_id != participant_id
    ]
    timelines = {t.participant_id: t for t in data.timelines}
    empty = ParticipantTimeline(0)

    def mains(pid: int) -> list[int]:
        return owned_main_items(timelines.get(pid, empty), anchor_ms)

    out: dict[str, Any] = {"minute": anchor_ms / 60_000}
    profile = champions.get(me.champion_id)
    out["self_champion_id"] = str(me.champion_id)
    out["self_role"] = me.role or "NONE"
    out["self_primary_tag"] = profile.tags[0] if profile and profile.tags else "NONE"
    out["self_magic_share"] = profile.magic_share if profile else NAN
    out["self_melee"] = float(profile.melee) if profile else NAN
    own_items = mains(participant_id)
    out.update(_side_items(own_items, items, "self"))
    boots = _boots(timelines.get(participant_id, empty), anchor_ms)
    out["self_has_boots"] = float(boots is not None)

    enemy_items = [i for p in enemies for i in mains(p.participant_id)]
    ally_items = [i for p in allies for i in mains(p.participant_id)]
    out.update(_side_items(enemy_items, items, "enemy"))
    out.update(_side_items(ally_items, items, "ally"))
    out.update(_side_champions([p.champion_id for p in enemies], champions, "enemy"))
    out.update(_side_champions([p.champion_id for p in allies], champions, "ally"))

    # Placar até a âncora.
    kills = [k for k in data.kills if k.timestamp_ms < anchor_ms]
    team_of = {p.participant_id: p.team for p in data.participants}
    enemy_ids = {p.participant_id for p in enemies}
    out["team_kills"] = float(sum(team_of.get(k.killer_id) == me.team for k in kills))
    out["enemy_kills"] = float(sum(k.killer_id in enemy_ids for k in kills))
    out["kill_diff"] = out["team_kills"] - out["enemy_kills"]
    out["self_kills"] = float(sum(k.killer_id == participant_id for k in kills))
    out["self_deaths"] = float(sum(k.victim_id == participant_id for k in kills))
    out["self_assists"] = float(sum(participant_id in k.assisting_ids for k in kills))
    enemy_kill_counts = Counter(k.killer_id for k in kills if k.killer_id in enemy_ids)
    top_enemy = max(
        enemies, key=lambda p: (enemy_kill_counts.get(p.participant_id, 0), -p.participant_id)
    )
    top_profile = champions.get(top_enemy.champion_id)
    out["top_enemy_kills"] = float(enemy_kill_counts.get(top_enemy.participant_id, 0))
    out["top_enemy_kill_share"] = (
        out["top_enemy_kills"] / out["enemy_kills"] if out["enemy_kills"] else 0.0
    )
    out["top_enemy_tag"] = (
        (top_profile.tags[0] if top_profile and top_profile.tags else "NONE")
        if out["top_enemy_kills"]
        else "NONE"
    )
    out["top_enemy_magic_share"] = (
        top_profile.magic_share if top_profile and out["top_enemy_kills"] else NAN
    )
    # Quem matou o jogador: perfil do abatedor em cada morte.
    deaths = [k for k in kills if k.victim_id == participant_id and k.killer_id in enemy_ids]
    killer_profiles = [champions.get(by_id[k.killer_id].champion_id) for k in deaths]
    out["deaths_to_magic"] = float(
        sum(p is not None and p.magic_share > 0.6 for p in killer_profiles)
    )
    out["deaths_to_physical"] = float(
        sum(p is not None and p.magic_share < 0.4 for p in killer_profiles)
    )
    out["deaths_to_assassin"] = float(
        sum(p is not None and "Assassin" in p.tags for p in killer_profiles)
    )
    out["deaths_to_melee"] = float(sum(p is not None and p.melee for p in killer_profiles))

    # Objetivos até a âncora (time que conquistou).
    objectives = [o for o in data.objectives if o.timestamp_ms < anchor_ms and o.team is not None]
    for kind, name in (("TOWER", "towers"), ("DRAGON", "dragons"), ("BARON", "barons")):
        mine = sum(o.kind == kind and o.team == me.team for o in objectives)
        theirs = sum(o.kind == kind and o.team != me.team for o in objectives)
        out[f"{name}_diff"] = float(mine - theirs)
    out["enemy_barons"] = float(sum(o.kind == "BARON" and o.team != me.team for o in objectives))

    # Ouro no último quadro antes da âncora.
    gold = _gold_at(data.gold_frames, anchor_ms)
    if gold:
        team_gold = sum(v for pid, v in gold.items() if team_of.get(pid) == me.team)
        enemy_gold = sum(v for pid, v in gold.items() if pid in enemy_ids)
        own = gold.get(participant_id)
        out["team_gold_diff"] = float(team_gold - enemy_gold)
        out["self_gold_share"] = own / team_gold if own is not None and team_gold else NAN
        opponent = next((p for p in enemies if me.role and p.role == me.role), None)
        opponent_gold = gold.get(opponent.participant_id) if opponent else None
        out["lane_gold_diff"] = (
            float(own - opponent_gold) if own is not None and opponent_gold is not None else NAN
        )
        top_gold = max((gold.get(p.participant_id, 0) for p in enemies), default=0)
        out["richest_enemy_gold_share"] = top_gold / enemy_gold if enemy_gold else NAN
    else:
        for name in (
            "team_gold_diff",
            "self_gold_share",
            "lane_gold_diff",
            "richest_enemy_gold_share",
        ):
            out[name] = NAN
    return out


__all__ = [
    "SITUATION_VERSION",
    "SituationInput",
    "owned_main_items",
    "situation_features",
]
