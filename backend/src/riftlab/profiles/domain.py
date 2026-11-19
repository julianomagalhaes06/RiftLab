"""Perfis de itens e campeões a partir do Data Dragon (CC-1502). Puro, versionado.

Para que servem: features do modelo de late game e fatos da explicação. Nunca decidem
legalidade, preço ou classificação de item (isso continua com a curadoria, que proíbe texto livre
como regra). O Data Dragon guarda em `stats` só parte dos atributos: aceleração de habilidade,
letalidade e penetrações aparecem apenas no bloco `<stats>` da descrição, então os atributos
vêm desse bloco. Mecânicas (feridas, estase, escudo de feitiço...) e traços de campeão (cura,
escudo, controle de grupo) são detectados por padrões fixos no texto em inglês, com a evidência
guardada para revisão.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from riftlab.shared.numeric import is_ascii_digits

PROFILE_VERSION = "profiles_v1"

# Nome no bloco <stats> (com " %" quando o valor é percentual) -> chave do perfil.
STAT_KEYS: Mapping[str, str] = {
    "Attack Damage": "ad",
    "Ability Power": "ap",
    "Attack Speed %": "attack_speed",
    "Critical Strike Chance %": "crit",
    "Critical Strike Damage %": "crit_damage",
    "Armor": "armor",
    "Magic Resist": "mr",
    "Health": "hp",
    "Mana": "mana",
    "Ability Haste": "haste",
    "Lethality": "lethality",
    "Armor Penetration %": "armor_pen",
    "Magic Penetration": "magic_pen",
    "Magic Penetration %": "magic_pen_pct",
    "Life Steal %": "lifesteal",
    "Omnivamp %": "omnivamp",
    "Move Speed": "move_speed",
    "Move Speed %": "move_speed_pct",
    "Heal and Shield Power %": "heal_shield_power",
    "Tenacity %": "tenacity",
    "Base Health Regen %": "hp_regen",
    "Base Mana Regen %": "mana_regen",
    "Adaptive Force": "adaptive",
}
ITEM_STATS = tuple(dict.fromkeys(STAT_KEYS.values()))

# Mecânica -> padrão no texto em inglês (minúsculo, sem marcação) da descrição do item.
ITEM_MECHANICS: Mapping[str, re.Pattern[str]] = {
    "ANTIHEAL": re.compile(r"\bwounds\b"),
    "STASIS": re.compile(r"\bstasis\b"),
    "SPELL_SHIELD": re.compile(r"\bspell shield\b"),
    "REVIVE": re.compile(r"\brebirth\b|\brevive"),
    "CLEANSE": re.compile(r"remov\w* all crowd control|\bcleanse"),
    "SHIELD": re.compile(r"(?<!spell )\bshield\b"),
    "ANTI_SHIELD": re.compile(r"reduc\w* (?:any |the )?shields?|shield reaver"),
    "SLOW": re.compile(r"\bslow"),
    "ARMOR_SHRED": re.compile(r"reduc\w* (?:their |the target's |enemy )?armor|armor reduction"),
    "MR_SHRED": re.compile(r"reduc\w* (?:their |the target's |enemy )?magic resist"),
    "EXECUTE": re.compile(r"\bexecut"),
    "ANTI_ATTACK_SPEED": re.compile(r"reduc\w* (?:the )?attack speed|attack speed (?:is )?reduc"),
    "ANTI_CRIT": re.compile(r"critical strike damage taken|damage taken from critical"),
    "MAGIC_SHIELD": re.compile(r"magic damage shield"),
}

# Traço de campeão -> padrão no texto das habilidades e da passiva (descrição, sem tooltip).
CHAMPION_TRAITS: Mapping[str, re.Pattern[str]] = {
    "HEALING": re.compile(
        r"\bheals?\b|\bhealing\b|restor\w* [^.]{0,40}health|\bomnivamp\b|\blife ?steal\b|\bdrain"
    ),
    "SHIELDING": re.compile(r"\bshield"),
    "HARD_CC": re.compile(
        r"\bstun|knock(?:s|ing)? (?:up|back|aside)|\bairborne|\broot|\bsuppress|\bcharm|\bfear"
        r"|\bflee|\btaunt|\bsleep|\bpolymorph|\bpull"
    ),
    "STEALTH": re.compile(r"\bcamouflage|\binvisib|\bstealth"),
    "DASH": re.compile(r"\bdash|\bleap|\bblink|\bteleport"),
}
# Frases que citam cura para REDUZI-LA não são cura (ex.: aplicar Feridas).
_HEAL_NEGATION = re.compile(r"wounds|reduc\w* (?:\w+ )?heal")
_TAG = re.compile(r"<[^>]+>")
_STATS_BLOCK = re.compile(r"<stats>(.*?)</stats>", re.S)
_STAT_ENTRY = re.compile(r"<attention>\s*([\d.]+)(%?)\s*</attention>\s*([^<]+)")


def _plain(html: str) -> str:
    return re.sub(r"\s+", " ", _TAG.sub(" ", html)).strip().lower()


@dataclass(frozen=True, slots=True)
class ItemProfile:
    item_id: int
    stats: Mapping[str, float]
    mechanics: frozenset[str]
    tags: tuple[str, ...] = ()

    def stat(self, key: str) -> float:
        return float(self.stats.get(key, 0.0))

    def to_payload(self) -> dict[str, Any]:
        return {
            "stats": dict(sorted(self.stats.items())),
            "mechanics": sorted(self.mechanics),
            "tags": list(self.tags),
        }


@dataclass(frozen=True, slots=True)
class ChampionProfile:
    champion_id: int
    text_id: str
    tags: tuple[str, ...]
    attack: int
    defense: int
    magic: int
    difficulty: int
    attack_range: float
    traits: frozenset[str] = field(default_factory=frozenset)
    hard_cc_spells: int = 0
    evidence: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    @property
    def melee(self) -> bool:
        return self.attack_range <= 350

    @property
    def magic_share(self) -> float:
        total = self.attack + self.magic
        return self.magic / total if total else 0.5

    def to_payload(self) -> dict[str, Any]:
        return {
            "textId": self.text_id,
            "tags": list(self.tags),
            "attack": self.attack,
            "defense": self.defense,
            "magic": self.magic,
            "difficulty": self.difficulty,
            "attackRange": self.attack_range,
            "traits": sorted(self.traits),
            "hardCcSpells": self.hard_cc_spells,
            "evidence": {k: list(v) for k, v in sorted(self.evidence.items())},
        }


def parse_item_stats(description: str) -> dict[str, float]:
    match = _STATS_BLOCK.search(description or "")
    stats: dict[str, float] = {}
    if match is None:
        return stats
    for value, percent, name in _STAT_ENTRY.findall(match.group(1)):
        key = STAT_KEYS.get(name.strip() + (" %" if percent else ""))
        if key is not None:
            stats[key] = stats.get(key, 0.0) + float(value)
    return stats


def parse_item_mechanics(description: str) -> frozenset[str]:
    text = _plain(_STATS_BLOCK.sub(" ", description or ""))
    return frozenset(name for name, pattern in ITEM_MECHANICS.items() if pattern.search(text))


def parse_item_profiles(item_document: Mapping[str, Any]) -> dict[int, ItemProfile]:
    profiles: dict[int, ItemProfile] = {}
    for raw_id, entry in (item_document.get("data") or {}).items():
        if not is_ascii_digits(str(raw_id)) or not isinstance(entry, dict):
            continue
        description = str(entry.get("description") or "")
        profiles[int(raw_id)] = ItemProfile(
            int(raw_id),
            parse_item_stats(description),
            parse_item_mechanics(description),
            tuple(sorted(str(t) for t in entry.get("tags") or ())),
        )
    return profiles


def _sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+", text) if s]


def parse_champion_profiles(champion_full: Mapping[str, Any]) -> dict[int, ChampionProfile]:
    """`championFull.json` em inglês: tags, notas de ataque/defesa/magia, alcance e traços."""
    profiles: dict[int, ChampionProfile] = {}
    for text_id, entry in (champion_full.get("data") or {}).items():
        if not isinstance(entry, dict) or not is_ascii_digits(str(entry.get("key", ""))):
            continue
        info = entry.get("info") or {}
        texts: list[tuple[str, str]] = []
        passive = entry.get("passive") or {}
        texts.append((str(passive.get("name") or "Passive"), str(passive.get("description") or "")))
        for spell in entry.get("spells") or ():
            texts.append((str(spell.get("name") or ""), str(spell.get("description") or "")))
        evidence: dict[str, list[str]] = {}
        hard_cc = 0
        for name, description in texts:
            plain = _plain(description)
            for trait, pattern in CHAMPION_TRAITS.items():
                if trait == "HEALING":
                    hit = any(
                        pattern.search(s) and not _HEAL_NEGATION.search(s)
                        for s in _sentences(plain)
                    )
                else:
                    hit = bool(pattern.search(plain))
                if hit:
                    evidence.setdefault(trait, []).append(name)
                    if trait == "HARD_CC":
                        hard_cc += 1
        champion_id = int(entry["key"])
        profiles[champion_id] = ChampionProfile(
            champion_id,
            str(text_id),
            tuple(str(t) for t in entry.get("tags") or ()),
            int(info.get("attack", 0)),
            int(info.get("defense", 0)),
            int(info.get("magic", 0)),
            int(info.get("difficulty", 0)),
            float((entry.get("stats") or {}).get("attackrange", 0)),
            frozenset(evidence),
            hard_cc,
            {k: tuple(v) for k, v in evidence.items()},
        )
    return profiles


@dataclass(frozen=True, slots=True)
class Profiles:
    """Perfis de um patch, com a origem (versão do Data Dragon e hash dos arquivos)."""

    patch: str
    data_dragon_version: str
    items: Mapping[int, ItemProfile]
    champions: Mapping[int, ChampionProfile]
    source_sha256: Mapping[str, str] = field(default_factory=dict)
    version: str = PROFILE_VERSION

    def to_document(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "patch": self.patch,
            "dataDragonVersion": self.data_dragon_version,
            "sourceSha256": dict(sorted(self.source_sha256.items())),
            "items": {str(k): v.to_payload() for k, v in sorted(self.items.items())},
            "champions": {str(k): v.to_payload() for k, v in sorted(self.champions.items())},
        }

    @classmethod
    def from_document(cls, document: Mapping[str, Any]) -> Profiles:
        items = {
            int(k): ItemProfile(
                int(k),
                {s: float(x) for s, x in v["stats"].items()},
                frozenset(v["mechanics"]),
                tuple(v.get("tags", ())),
            )
            for k, v in document["items"].items()
        }
        champions = {
            int(k): ChampionProfile(
                int(k),
                v["textId"],
                tuple(v["tags"]),
                int(v["attack"]),
                int(v["defense"]),
                int(v["magic"]),
                int(v["difficulty"]),
                float(v["attackRange"]),
                frozenset(v["traits"]),
                int(v["hardCcSpells"]),
                {t: tuple(e) for t, e in v.get("evidence", {}).items()},
            )
            for k, v in document["champions"].items()
        }
        return cls(
            document["patch"],
            document["dataDragonVersion"],
            items,
            champions,
            dict(document.get("sourceSha256", {})),
            document.get("version", PROFILE_VERSION),
        )


def build_profiles(
    patch: str, data_dragon_version: str, item_bytes: bytes, champion_full_bytes: bytes
) -> Profiles:
    return Profiles(
        patch,
        data_dragon_version,
        parse_item_profiles(json.loads(item_bytes)),
        parse_champion_profiles(json.loads(champion_full_bytes)),
        {
            "item": hashlib.sha256(item_bytes).hexdigest(),
            "championFull": hashlib.sha256(champion_full_bytes).hexdigest(),
        },
    )


def profiles_path(configs: Path, patch: str) -> Path:
    return configs / "profiles" / f"profiles-{patch}.json"


def load_profiles(configs: Path, patch: str) -> Profiles | None:
    """Os perfis do patch, senão os do patch anterior mais recente (atributos de campeão e de
    item mudam pouco entre patches; item novo sem perfil fica ausente, nunca zero)."""
    from riftlab.shared.numeric import patch_order_key

    own = profiles_path(configs, patch)
    if own.exists():
        return Profiles.from_document(json.loads(own.read_text(encoding="utf-8")))
    key = patch_order_key(patch)
    if key is None:
        return None
    earlier = sorted(
        (
            (k, p)
            for p in (configs / "profiles").glob("profiles-*.json")
            if (k := patch_order_key(p.stem.removeprefix("profiles-"))) is not None and k < key
        ),
        reverse=True,
    )
    for _, path in earlier:
        return Profiles.from_document(json.loads(path.read_text(encoding="utf-8")))
    return None


__all__ = [
    "CHAMPION_TRAITS",
    "ITEM_MECHANICS",
    "ITEM_STATS",
    "PROFILE_VERSION",
    "ChampionProfile",
    "ItemProfile",
    "Profiles",
    "build_profiles",
    "load_profiles",
    "parse_champion_profiles",
    "parse_item_mechanics",
    "parse_item_profiles",
    "parse_item_stats",
    "profiles_path",
]
