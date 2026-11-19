"""Base de conhecimento do RAG (CC-1505): trechos, cartões e agregados de casos. Puro.

Três fontes, cada trecho com id estável para citação:
- `item:<id>`: nome, texto e descrição oficial do item em português (Data Dragon pt_BR).
- `ability:<campeão>:<P|Q|W|E|R>`: passiva e habilidades em português.
- `card:<id>`: cartões curados (`configs/knowledge/cards-v1.yaml`) com gatilho verificável.
Os agregados de casos (`cases`) vêm das linhas de treino e só existem com amostra mínima.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from riftlab.shared.numeric import is_ascii_digits

KNOWLEDGE_VERSION = "knowledge_v1"
_TAG = re.compile(r"<[^>]+>")
_SLOTS = ("Q", "W", "E", "R")
_OPS = {
    ">=": lambda a, b: a >= b,
    "<=": lambda a, b: a <= b,
    ">": lambda a, b: a > b,
    "<": lambda a, b: a < b,
    "==": lambda a, b: a == b,
}


def plain(html: str) -> str:
    text = _TAG.sub(" ", (html or "").replace("<br>", ". "))
    text = re.sub(r"\s+", " ", text).replace(" .", ".")
    return re.sub(r"\.(?:\s*\.)+", ".", text).strip(" .") + "."


@dataclass(frozen=True, slots=True)
class Chunk:
    id: str
    kind: str  # ITEM | ABILITY | CARD
    title: str
    text: str
    item_id: int | None = None
    champion_id: int | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "title": self.title,
            "text": self.text,
            "itemId": self.item_id,
            "championId": self.champion_id,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> Chunk:
        return cls(
            payload["id"],
            payload["kind"],
            payload["title"],
            payload["text"],
            payload.get("itemId"),
            payload.get("championId"),
        )


@dataclass(frozen=True, slots=True)
class Card:
    id: str
    title: str
    text: str
    source: str
    mechanics: frozenset[str]
    stats: Mapping[str, float]
    when: Mapping[str, Sequence[Sequence[Any]]]

    def triggered(self, context: Mapping[str, Any]) -> bool:
        def check(condition: Sequence[Any]) -> bool:
            name, op, threshold = condition
            value = context.get(name)
            if not isinstance(value, (int, float)) or isinstance(value, bool) or math.isnan(value):
                return False
            return bool(_OPS[op](float(value), float(threshold)))

        if "all" in self.when:
            return all(check(c) for c in self.when["all"])
        return any(check(c) for c in self.when.get("any", ()))

    def fits(self, mechanics: Iterable[str], stats: Mapping[str, float]) -> bool:
        if self.mechanics & set(mechanics):
            return True
        return any(float(stats.get(k, 0.0)) >= v for k, v in self.stats.items())

    def chunk(self) -> Chunk:
        return Chunk(f"card:{self.id}", "CARD", self.title, f"{self.text} Fonte: {self.source}")


def load_cards(path: Path) -> tuple[str, list[Card]]:
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    cards = []
    for raw in document["cards"]:
        when = raw["when"]
        if set(when) - {"any", "all"} or not when:
            raise ValueError(f"cartão {raw['id']}: condição inválida")
        for group in when.values():
            for condition in group:
                if len(condition) != 3 or condition[1] not in _OPS:
                    raise ValueError(f"cartão {raw['id']}: condição inválida")
        cards.append(
            Card(
                raw["id"],
                raw["title"],
                " ".join(str(raw["text"]).split()),
                raw["source"],
                frozenset(raw.get("mechanics", ())),
                {k: float(v) for k, v in (raw.get("stats") or {}).items()},
                when,
            )
        )
    return document["version"], cards


def item_chunks(item_document: Mapping[str, Any], item_ids: Iterable[int]) -> list[Chunk]:
    data = item_document.get("data") or {}
    out = []
    for item_id in sorted(set(item_ids)):
        entry = data.get(str(item_id))
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or item_id)
        description = plain(str(entry.get("description") or ""))
        out.append(
            Chunk(f"item:{item_id}", "ITEM", name, f"{name}: {description}", item_id=item_id)
        )
    return out


def ability_chunks(champion_full: Mapping[str, Any]) -> list[Chunk]:
    out = []
    for entry in (champion_full.get("data") or {}).values():
        if not isinstance(entry, dict) or not is_ascii_digits(str(entry.get("key", ""))):
            continue
        champion_id = int(entry["key"])
        name = str(entry.get("name") or entry.get("id"))
        passive = entry.get("passive") or {}
        parts = [("P", passive.get("name"), passive.get("description"))]
        for slot, spell in zip(_SLOTS, entry.get("spells") or (), strict=False):
            parts.append((slot, spell.get("name"), spell.get("description")))
        for slot, title, description in parts:
            if not title or not description:
                continue
            out.append(
                Chunk(
                    f"ability:{champion_id}:{slot}",
                    "ABILITY",
                    f"{name} — {title} ({slot})",
                    f"{name}, {title} ({slot}): {plain(str(description))}",
                    champion_id=champion_id,
                )
            )
    return out


# ------------------------------------------------------------------ BM25 (pergunta livre)

_STOP = frozenset(
    "a o as os de da do das dos e em no na nos nas um uma uns umas por para com sem que se ao aos "
    "à às é ser foi como mais menos muito pouco eu voce você meu minha seu sua ele ela isso esse "
    "essa este esta qual quais quando onde porque por que nao não sim tem ter".split()
)


def tokens(text: str) -> list[str]:
    folded = unicodedata.normalize("NFKD", text.lower())
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    return [t for t in re.findall(r"[a-z0-9]+", folded) if t not in _STOP and len(t) > 1]


@dataclass
class Bm25:
    chunks: list[Chunk]
    k1: float = 1.4
    b: float = 0.75
    _docs: list[Counter[str]] = field(default_factory=list)
    _df: Counter[str] = field(default_factory=Counter)
    _avg: float = 1.0

    def __post_init__(self) -> None:
        self._docs = [Counter(tokens(f"{c.title} {c.text}")) for c in self.chunks]
        for doc in self._docs:
            self._df.update(doc.keys())
        self._avg = sum(sum(d.values()) for d in self._docs) / max(len(self._docs), 1)

    def search(self, query: str, limit: int = 5, allowed: set[str] | None = None) -> list[Chunk]:
        terms = tokens(query)
        n = len(self.chunks)
        scored: list[tuple[float, str, Chunk]] = []
        for chunk, doc in zip(self.chunks, self._docs, strict=True):
            if allowed is not None and chunk.id not in allowed:
                continue
            length = sum(doc.values())
            score = 0.0
            for term in terms:
                tf = doc.get(term, 0)
                if not tf:
                    continue
                idf = math.log(1 + (n - self._df[term] + 0.5) / (self._df[term] + 0.5))
                score += (
                    idf
                    * tf
                    * (self.k1 + 1)
                    / (tf + self.k1 * (1 - self.b + self.b * length / self._avg))
                )
            if score > 0:
                scored.append((score, chunk.id, chunk))
        scored.sort(key=lambda x: (-x[0], x[1]))
        return [c for _, _, c in scored[:limit]]


# ------------------------------------------------------------------ agregados de casos

SIGNATURE_FEATURES = (
    ("cura", lambda c: _num(c, "enemy_healers") >= 2 or _num(c, "enemy_items_sustain") >= 15),
    ("magico", lambda c: _num(c, "enemy_magic_share") >= 0.6),
    ("fisico", lambda c: 0 <= _num(c, "enemy_magic_share") <= 0.4),
    ("armadura", lambda c: _num(c, "enemy_items_armor") >= 150),
    ("assassinos", lambda c: _num(c, "enemy_assassin") >= 2),
    ("atras", lambda c: _num(c, "team_gold_diff") <= -3000),
    ("frente", lambda c: _num(c, "team_gold_diff") >= 3000),
)
SIGNATURE_TEXT = {
    "cura": "inimigos com cura",
    "magico": "dano inimigo majoritariamente mágico",
    "fisico": "dano inimigo majoritariamente físico",
    "armadura": "inimigos com muita Armadura",
    "assassinos": "dois ou mais assassinos inimigos",
    "atras": "time atrás no ouro",
    "frente": "time à frente no ouro",
}
MIN_CHAMPION_CASES = 20
MIN_ROLE_CASES = 50


def _num(context: Mapping[str, Any], key: str) -> float:
    value = context.get(key)
    if isinstance(value, (int, float)) and not isinstance(value, bool) and not math.isnan(value):
        return float(value)
    return -1.0


def signature(context: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(name for name, test in SIGNATURE_FEATURES if test(context))


def build_cases(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Contagens do alvo por (campeão|rota, ordem, assinatura), só do treino; células abaixo do
    mínimo são descartadas (nenhuma partida individual sai daqui)."""
    champion: dict[str, Counter[int]] = {}
    role: dict[str, Counter[int]] = {}
    for row in rows:
        if row["split"] != "train" or not row.get("targetLegal"):
            continue
        sig = "+".join(signature(row["context"]))
        champion.setdefault(f"{row['championId']}|{row['ordinal']}|{sig}", Counter())[
            row["targetItemId"]
        ] += 1
        role.setdefault(f"{row.get('role')}|{row['ordinal']}|{sig}", Counter())[
            row["targetItemId"]
        ] += 1

    def keep(table: dict[str, Counter[int]], minimum: int) -> dict[str, dict[str, int]]:
        return {
            k: {str(i): n for i, n in v.most_common(8)} | {"_n": sum(v.values())}
            for k, v in table.items()
            if sum(v.values()) >= minimum
        }

    return {
        "version": KNOWLEDGE_VERSION,
        "minChampion": MIN_CHAMPION_CASES,
        "minRole": MIN_ROLE_CASES,
        "champion": keep(champion, MIN_CHAMPION_CASES),
        "role": keep(role, MIN_ROLE_CASES),
    }


@dataclass(frozen=True)
class KnowledgeBase:
    patch: str
    chunks: Mapping[str, Chunk]
    cards: Sequence[Card]
    cases: Mapping[str, Any]
    version: str = KNOWLEDGE_VERSION

    @classmethod
    def load(cls, directory: Path, cards_path: Path) -> KnowledgeBase:
        document = json.loads((directory / "knowledge.json").read_text(encoding="utf-8"))
        cases_path = directory / "cases.json"
        cases = json.loads(cases_path.read_text()) if cases_path.exists() else {}
        _, cards = load_cards(cards_path)
        chunks = {c["id"]: Chunk.from_payload(c) for c in document["chunks"]}
        for card in cards:
            chunks[card.chunk().id] = card.chunk()
        return cls(document["patch"], chunks, cards, cases)

    def item_name(self, item_id: int, fallback: str | None = None) -> str:
        """Nome em português do trecho do Data Dragon pt_BR; senão o do catálogo."""
        chunk = self.chunks.get(f"item:{item_id}")
        return chunk.title if chunk is not None else (fallback or str(item_id))

    def case_fact(
        self,
        champion_id: int,
        role: str | None,
        ordinal: int,
        context: Mapping[str, Any],
        item_id: int,
        names: Mapping[int, str | None],
    ) -> dict[str, Any] | None:
        """Os itens mais comprados em situações parecidas (só células com amostra mínima)."""
        sig = signature(context)
        key = "+".join(sig)
        for scope, table, label in (
            ("champion", self.cases.get("champion", {}), "deste campeão"),
            ("role", self.cases.get("role", {}), "da mesma rota"),
        ):
            cell = table.get(f"{champion_id if scope == 'champion' else role}|{ordinal}|{key}")
            if not cell:
                continue
            n = int(cell["_n"])
            top = sorted(
                ((int(i), c) for i, c in cell.items() if i != "_n"), key=lambda x: (-x[1], x[0])
            )[:3]
            situation = ", ".join(SIGNATURE_TEXT[s] for s in sig) or "sem traço marcante"
            listed = ", ".join(f"{self.item_name(i, names.get(i))} ({c / n:.0%})" for i, c in top)
            share = cell.get(str(item_id), 0) / n
            focus = (
                ""
                if any(i == item_id for i, _ in top)
                else f" {self.item_name(item_id, names.get(item_id))} apareceu em {share:.0%}."
            )
            return {
                "id": f"case:{scope}:{ordinal}:{key or 'base'}:{item_id}",
                "kind": "CASE",
                "title": f"Situações parecidas ({label})",
                "text": f"Em {n} decisões parecidas {label} como {ordinal}º item principal "
                f"({situation}), os mais comprados foram {listed}.{focus}",
            }
        return None


def build_knowledge_document(
    patch: str,
    data_dragon_version: str,
    item_bytes: bytes,
    champion_bytes: bytes,
    item_ids: Iterable[int],
) -> dict[str, Any]:
    chunks = item_chunks(json.loads(item_bytes), item_ids) + ability_chunks(
        json.loads(champion_bytes)
    )
    return {
        "version": KNOWLEDGE_VERSION,
        "patch": patch,
        "dataDragonVersion": data_dragon_version,
        "locale": "pt_BR",
        "sourceSha256": {
            "item": hashlib.sha256(item_bytes).hexdigest(),
            "championFull": hashlib.sha256(champion_bytes).hexdigest(),
        },
        "chunks": [c.to_payload() for c in chunks],
    }


__all__ = [
    "KNOWLEDGE_VERSION",
    "Bm25",
    "Card",
    "Chunk",
    "KnowledgeBase",
    "ability_chunks",
    "build_cases",
    "build_knowledge_document",
    "item_chunks",
    "load_cards",
    "plain",
    "signature",
    "tokens",
]
