"""Explicação fundamentada (CC-1505): fatos recuperados + resposta validada. Puro.

O ranking decide; a explicação só pode dizer o que está nos fatos. Cada frase cita ids de
fatos; um validador descarta a frase que cita fato inexistente, traz número que não aparece nos
fatos citados ou menciona item/campeão que os fatos citados não mencionam. Sem frase válida (ou
sem modelo), a resposta é a determinística, montada dos mesmos fatos.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from riftlab.knowledge.base import Bm25, KnowledgeBase
from riftlab.profiles.domain import ChampionProfile, ItemProfile

EXPLANATION_VERSION = "explanation_v1"
MAX_FACTS = 12
MAX_SENTENCES = 4
MAX_SENTENCE_CHARS = 320
MAX_QUESTION_CHARS = 300
_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")
_WORD = re.compile(r"[a-z]{5,}")
# Fração mínima das palavras de conteúdo da frase (5+ letras, comparadas pelo radical de 5
# letras) que precisa aparecer nos fatos citados: barra raciocínio inventado com número certo.
MIN_LEXICAL_SUPPORT = 0.6
_CONNECTORS = frozenset(
    "porque quando ainda assim entao mesmo nesta neste essa esse isso pelos pelas sobre "
    "partida indicado recomendado melhor ajuda ajudam vale valem item itens".split()
)


@dataclass(frozen=True, slots=True)
class Fact:
    id: str
    kind: str
    title: str
    text: str

    def to_payload(self) -> dict[str, str]:
        return {"id": self.id, "kind": self.kind, "title": self.title, "text": self.text}


def _fold(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in folded if not unicodedata.combining(c))


def gather_facts(
    kb: KnowledgeBase,
    analysis: Mapping[str, Any],
    context: Mapping[str, Any],
    *,
    champion_id: int,
    role: str | None,
    enemy_champion_ids: Sequence[int],
    names: Mapping[int, str | None],
    items: Mapping[int, ItemProfile],
    champions: Mapping[int, ChampionProfile],
) -> list[Fact]:
    """Fatos da decisão, em ordem de importância: resumo do modelo, gatilhos, cartões,
    situações parecidas, itens e habilidades inimigas relevantes."""
    candidates = list(analysis.get("candidates") or [])
    late = analysis.get("late") or {}
    ordinal = int(late.get("ordinal") or 0)
    facts: list[Fact] = []
    if not candidates:
        return facts
    top = candidates[0]
    default = next((c for c in candidates if c.get("isDefault")), None)

    def name(candidate: Mapping[str, Any]) -> str:
        return kb.item_name(int(candidate["itemId"]), candidate.get("name"))

    summary = (
        f"Para esta partida, o {ordinal}º item principal mais indicado é {name(top)}"
        + (f" ({top['probability']:.0%})" if isinstance(top.get("probability"), float) else "")
        + "."
    )
    if default is not None and default["itemId"] != top["itemId"]:
        summary += f" O item mais comum para este campeão nesta etapa é {default.get('name')}" + (
            f" ({default['baseProbability']:.0%} no padrão)."
            if isinstance(default.get("baseProbability"), float)
            else "."
        )
    facts.append(Fact("analysis:summary", "ANALYSIS", "Resultado da análise", summary))
    for candidate in candidates:
        for explanation in candidate.get("explanations") or []:
            source = str(explanation.get("source", ""))
            if ".m_" in source or source.startswith("late_analysis_v1."):
                facts.append(
                    Fact(
                        f"trigger:{candidate['itemId']}:{source.rsplit('.', 1)[-1]}",
                        "TRIGGER",
                        f"Gatilho de {name(candidate)}",
                        str(explanation["text"]),
                    )
                )
    context_with_ordinal = {**context, "ordinal": ordinal}
    focus = [top] + ([default] if default is not None and default is not top else [])
    seen_cards: set[str] = set()
    for candidate in focus:
        profile = items.get(int(candidate["itemId"]))
        if profile is None:
            continue
        for card in kb.cards:
            if card.id in seen_cards:
                continue
            if card.triggered(context_with_ordinal) and card.fits(profile.mechanics, profile.stats):
                card_chunk = card.chunk()
                facts.append(Fact(card_chunk.id, "CARD", card_chunk.title, card_chunk.text))
                seen_cards.add(card.id)
    for candidate in focus:
        case = kb.case_fact(champion_id, role, ordinal, context, int(candidate["itemId"]), names)
        if case is not None:
            facts.append(Fact(case["id"], "CASE", case["title"], case["text"]))
    for candidate in focus:
        chunk = kb.chunks.get(f"item:{candidate['itemId']}")
        if chunk is not None:
            facts.append(Fact(chunk.id, "ITEM", chunk.title, chunk.text))
    if "antiheal" in seen_cards:
        healers = [c for c in enemy_champion_ids if "HEALING" in champions.get(c, _NO).traits]
        for champion in healers[:2]:
            evidence = set(champions[champion].evidence.get("HEALING", ()))
            for slot in ("P", "Q", "W", "E", "R"):
                chunk = kb.chunks.get(f"ability:{champion}:{slot}")
                if chunk is not None and any(name in chunk.title for name in evidence):
                    facts.append(Fact(chunk.id, "ABILITY", chunk.title, chunk.text))
                    break
    unique: dict[str, Fact] = {}
    for fact in facts:
        unique.setdefault(fact.id, fact)
    return list(unique.values())[:MAX_FACTS]


_NO = ChampionProfile(0, "", (), 0, 0, 0, 0, 0.0)


def question_facts(
    kb: KnowledgeBase, question: str, base: Sequence[Fact], limit: int = 3
) -> list[Fact]:
    """Trechos extras para uma pergunta livre (BM25 sobre itens, habilidades e cartões)."""
    have = {f.id for f in base}
    index = Bm25(list(kb.chunks.values()))
    extra = [
        Fact(c.id, c.kind, c.title, c.text)
        for c in index.search(question, limit + len(have))
        if c.id not in have
    ]
    return extra[:limit]


def deterministic_answer(facts: Sequence[Fact]) -> list[dict[str, Any]]:
    """Até 4 frases: resumo, gatilhos, um cartão e uma situação parecida, sempre com a fonte."""
    out: list[dict[str, Any]] = []
    by_kind: dict[str, list[Fact]] = {}
    for fact in facts:
        by_kind.setdefault(fact.kind, []).append(fact)
    for kind, take in (("ANALYSIS", 1), ("TRIGGER", 2), ("CARD", 1), ("CASE", 1)):
        for fact in by_kind.get(kind, [])[:take]:
            text = fact.text.split(" Fonte:")[0]
            out.append({"text": text, "sources": [fact.id]})
    return out[: MAX_SENTENCES + 1]


def build_prompt(facts: Sequence[Fact], question: str | None, ordinal: int) -> list[dict[str, str]]:
    numbered = "\n".join(f"[{f.id}] ({f.kind}) {f.text}" for f in facts)
    asked = question or "Por que o item mais indicado faz sentido nesta partida?"
    return [
        {
            "role": "system",
            "content": (
                "Você é um treinador de League of Legends que explica decisões de compra. "
                "Responda em português do Brasil, em no máximo 4 frases curtas, usando SOMENTE os "
                "fatos fornecidos. Cada frase cita os ids dos fatos que usou. Não invente números, "
                "itens nem campeões. Se os fatos não respondem, diga isso em uma frase. Responda "
                'apenas JSON: {"frases":[{"texto":"...","fontes":["id"]}]}'
            ),
        },
        {
            "role": "user",
            "content": (
                f"Fatos:\n{numbered}\n\nDecisão: {ordinal}º item principal.\nPergunta: {asked}"
            ),
        },
    ]


def lexical_support(folded_text: str, folded_cited: str) -> float:
    """Fração das palavras de conteúdo da frase cujo radical (5 letras) está nos fatos citados."""
    stems = {w[:5] for w in _WORD.findall(folded_cited)}
    words = [w for w in _WORD.findall(folded_text) if w not in _CONNECTORS]
    if not words:
        return 1.0
    return sum(w[:5] in stems for w in words) / len(words)


def validate_answer(
    raw: str, facts: Sequence[Fact], known_names: Iterable[str]
) -> tuple[list[dict[str, Any]], list[str]]:
    """(frases válidas, códigos das descartadas)."""
    by_id = {f.id: f for f in facts}
    try:
        payload = json.loads(raw.strip())
    except ValueError:
        return [], ["INVALID_JSON"]
    sentences = payload.get("frases") if isinstance(payload, dict) else None
    if not isinstance(sentences, list):
        return [], ["INVALID_SHAPE"]
    names = sorted({n for n in known_names if n and len(n) >= 4}, key=len, reverse=True)
    valid: list[dict[str, Any]] = []
    dropped: list[str] = []
    for sentence in sentences[:MAX_SENTENCES]:
        text = sentence.get("texto") if isinstance(sentence, dict) else None
        sources = sentence.get("fontes") if isinstance(sentence, dict) else None
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_SENTENCE_CHARS:
            dropped.append("INVALID_SENTENCE")
            continue
        if (
            not isinstance(sources, list)
            or not sources
            or not all(isinstance(s, str) and s in by_id for s in sources)
        ):
            dropped.append("UNKNOWN_SOURCE")
            continue
        cited = " ".join(by_id[s].text for s in sources)
        cited_numbers = {n.replace(",", ".") for n in _NUMBER.findall(cited)}
        if any(n.replace(",", ".") not in cited_numbers for n in _NUMBER.findall(text)):
            dropped.append("NUMBER_NOT_IN_SOURCES")
            continue
        folded_text, folded_cited = _fold(text), _fold(cited)
        if any(_fold(name) in folded_text and _fold(name) not in folded_cited for name in names):
            dropped.append("NAME_NOT_IN_SOURCES")
            continue
        if lexical_support(folded_text, folded_cited) < MIN_LEXICAL_SUPPORT:
            dropped.append("WORDS_NOT_IN_SOURCES")
            continue
        valid.append({"text": text.strip(), "sources": list(dict.fromkeys(sources))})
    return valid, dropped


__all__ = [
    "EXPLANATION_VERSION",
    "MAX_QUESTION_CHARS",
    "Fact",
    "build_prompt",
    "deterministic_answer",
    "gather_facts",
    "lexical_support",
    "question_facts",
    "validate_answer",
]
