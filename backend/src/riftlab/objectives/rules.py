"""CC-504: deterministic objective interpreter (provider RULES), no language model.

Follows ml/objectives/corpus/RUBRIC.md: instructions to the interpreter and texts with no
priority are NEEDS_INPUT; negated or deferred phrases do not count; an explicit precedence
marker picks the segment that follows it; two priorities without precedence are NEEDS_INPUT.
It is the baseline of the CC-503 comparison and the default provider when no model is
configured. Developed against the train split only.
"""

from __future__ import annotations

import re
import unicodedata

RULES_VERSION = "rules_v1"
BALANCED, SURVIVAL, DAMAGE, UTILITY = "BALANCED", "SURVIVAL", "DAMAGE", "UTILITY"
NEEDS_INPUT = "NEEDS_INPUT"

# Ordem importa: padrões multipalavra antes dos curtos; cada trecho do texto conta uma vez.
PATTERNS: tuple[tuple[str, str], ...] = (
    # negação que pede a própria prioridade ("não morrer" é sobreviver)
    (SURVIVAL, r"nao (quero )?(morrer|ser focad\w*|tomar one ?shot|cair)|parar de cair|ficar vivo"),
    (UTILITY, r"(nao )?(ficar |fico |acabo )?sem mana|falta de mana"),
    # situações cujas palavras pertenceriam a outra classe
    (SURVIVAL, r"me (mata|matam|explode|estoura|estouram)|pula em mim|tom(ar|ando) one ?shot"),
    (SURVIVAL, r"levo muito dano|muito dano (fisico|magico)|muito burst|me proteger|me defender"),
    (SURVIVAL, r"(aguentar|segurar) o dano|aguentar (o )?burst|resistir ao burst|sair vivo"),
    (SURVIVAL, r"defesa contra dano \w+|protecao contra \w+"),
    (DAMAGE, r"pouca vida e nao mato|nao morre nunca|continua de pe|sai das lutas sempre vivo"),
    (DAMAGE, r"muita armadura|ignora meu dano|matar tanque|acabar com as lutas"),
    (DAMAGE, r"velocidade de ataque|poder de (habilidade|fogo)|penetracao( de armadura| magica)?"),
    (DAMAGE, r"letalidade|matar antes de morrer"),
    (UTILITY, r"velocidade de movimento|aceleracao de habilidade|(tempo de |reducao de )?recarga"),
    (UTILITY, r"proteger (os |meus |o )?(aliados|time|atirador)|ajudar|suporte|peel|aura"),
    (UTILITY, r"habilidades? mais vezes|habilidades (demoram|voltarem)|girar|skills?|ultimate"),
    (UTILITY, r"correr|andar mais|rotac|chegar (antes|primeiro)|chego (atrasado|tarde)|\butil"),
    (UTILITY, r"\bmana\b|cooldown|\bcdr\b|pelo time"),
    (SURVIVAL, r"aguent|sobreviv|\bmorr|\bcaio\b|\bcair\b|tank|resist|armadura|\bvida\b"),
    (SURVIVAL, r"defe(sa|nsiv|nder)|protec|seguranca|\bvivo\b|durar|focad|burst|one ?shot"),
    (DAMAGE, r"\bdano\b|\bdps\b|\bmata(r)?\b|\bmato\b|critic|derret|explodir|estourar|abate"),
    (DAMAGE, r"ofensiv|bat(er|o)\b|estrago|agressiv"),
)
BALANCED_PATTERN = (
    r"equilibr|balance|um pouco de tudo|tanto faz|padrao|sem preferencia|nada em especial|"
    r"meio termo|neutr|normal|comum|todo mundo faz|tradicional|de sempre|usual|distribu|dividido|"
    r"sem prioridade|nao quero priorizar|nao tenho preferencia"
)
INJECTION_PATTERN = (
    r"ignor[ae] (as|tudo|o|a|suas) |esquec[ae] (as|tudo|suas) |regras|instruc|prompt|sistema|"
    r"system|json|yaml|xml|admin|execut|comando|imprim|variave|chave|senha|finja|voce (agora )?e|"
    r"responda|retorne|devolva|override|priority|needs_input|apague|banco de dados|[{}<>#=]|"
    r"\.env|rm -rf|traduz[ai]|revel|desativ|validacao|duas respostas|ultimo usuario"
)
_STOP = r"(?=[,;.]|\s(quero|preciso|prefiro|o negocio|so|mas|e)\s|$)"
# Gatilho antes do trecho negado ("não quero X"): o próprio gatilho não nega o que começa
# nele ("não morrer" é sobreviver).
TRIGGER_SPANS = (
    r"(nao|nem|sem|nada de|esquece|chega de|tudo menos)\s.*?" + _STOP,
    r"nao e .+? que eu quero",
)
# Trecho anterior ao marcador ("X não importa agora", "X seria bom"): tudo nele é negado ou adiado.
PREFIX_SPANS = (
    r"(^|[,;.]\s?)[^,;.]*?\snao importa( agora)?",
    r"(^|[,;.]\s?)[^,;.]*?\s(seria bom|fica pra depois|eu ja consigo)",
)
PRECEDENCE = (
    r"principalmente|mais importante (pra mim )?e|prioridade (e|:)|fico com|acima de tudo|"
    r"o que falta e|o negocio e|prefiro"
)


def normalise(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9{}<>#=_.,;: ]+", " ", text)).strip()


def _classes(text: str) -> tuple[set[str], set[str]]:
    """(classes afirmadas, classes negadas ou adiadas) num trecho normalizado."""
    triggers = [(m.start(), m.end()) for p in TRIGGER_SPANS for m in re.finditer(p, text)]
    prefixes = [(m.start(), m.end()) for p in PREFIX_SPANS for m in re.finditer(p, text)]
    taken = [False] * len(text)
    affirmed: set[str] = set()
    negated: set[str] = set()
    for label, pattern in PATTERNS:
        for match in re.finditer(pattern, text):
            if any(taken[match.start() : match.end()]):
                continue
            for i in range(match.start(), match.end()):
                taken[i] = True
            inside = any(a < match.start() < b for a, b in triggers) or any(
                a <= match.start() < b for a, b in prefixes
            )
            (negated if inside else affirmed).add(label)
    return affirmed, negated


def interpret(text: str) -> str:
    """Return one of BALANCED, SURVIVAL, DAMAGE, UTILITY or NEEDS_INPUT."""
    norm = normalise(text)
    if not norm or re.search(INJECTION_PATTERN, norm):
        return NEEDS_INPUT
    markers = list(re.finditer(PRECEDENCE, norm))
    if markers:
        focus = re.split(r"[,;.]| mesmo | mas ", norm[markers[-1].end() :].strip())[0]
        affirmed, _ = _classes(focus)
        if len(affirmed) == 1:
            return affirmed.pop()
    affirmed, _ = _classes(norm)
    balanced = bool(re.search(BALANCED_PATTERN, norm))
    if balanced and (not affirmed or re.search(r"\bnem\b|dividido", norm)):
        return BALANCED
    if len(affirmed) == 1:
        return affirmed.pop()
    return NEEDS_INPUT
