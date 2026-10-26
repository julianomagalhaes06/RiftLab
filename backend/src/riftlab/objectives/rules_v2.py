"""CC-1401: deterministic objective interpreter v2 (provider RULES), no language model.

Same structure as rules_v1 (riftlab.objectives.rules, frozen). Developed against the used test
sets v1/v2 and the validation splits, never against the test v3, and frozen (hash recorded)
before the test v3 is written. It only becomes the default after passing gate v3.

v1 notes follow.

Follows ml/objectives/corpus/RUBRIC.md: instructions to the interpreter and texts with no
priority are NEEDS_INPUT; negated or deferred phrases do not count; an explicit precedence
marker picks the segment that follows it; two priorities without precedence are NEEDS_INPUT.
It is the baseline of the CC-503 comparison and the default provider when no model is
configured. Developed against the train split only.
"""

from __future__ import annotations

import re
import unicodedata

RULES_VERSION = "rules_v2"
BALANCED, SURVIVAL, DAMAGE, UTILITY = "BALANCED", "SURVIVAL", "DAMAGE", "UTILITY"
NEEDS_INPUT = "NEEDS_INPUT"

# Ordem importa: padrões multipalavra antes dos curtos; cada trecho do texto conta uma vez.
PATTERNS: tuple[tuple[str, str], ...] = (
    # v2: situações com negação que pedem a própria prioridade
    (DAMAGE, r"ninguem morre|nao consigo (matar|terminar|fechar|finalizar)\w*|nao mato ninguem"),
    (DAMAGE, r"(dar|dou|de) (um )?one ?shot( (no|na|nos|nas|em) \w+( deles)?)?|fio de vida"),
    (
        DAMAGE,
        r"escapa\w* com|tira\w* (so )?metade( da vida)?|quero que (mate|morra)|nao faz cocegas",
    ),
    (DAMAGE, r"caus(ei|o|ar|a)\b|(quase )?nada de dano|pouco dano"),
    (DAMAGE, r"matar (o|a|os|as) \w+( deles)?|(ficam|fica) com pouca vida|fazendo armadura"),
    (UTILITY, r"nao consigo ajudar|cura\w* (nos|os|meus|o) aliados|escudo\w*"),
    (SURVIVAL, r"levando (muito )?dano|tomo muito dano|derreto|fico no chao|nao aguento"),
    (SURVIVAL, r"tank\w* (o )?dano|(ser|ficar) (mais )?tanque"),
    (SURVIVAL, r"nao tenho chance|me pega\w*|me (curar|cura)\b|curar\b"),
    # negação que pede a própria prioridade ("não morrer" é sobreviver)
    (
        SURVIVAL,
        r"nao (quero )?(morrer|ser focad\w*|tomar one ?shot|cair)|parar de (cair|morrer)|"
        r"ficar vivo",
    ),
    (UTILITY, r"(ficar|fico|acabo|fica|acaba) sem mana|falta de mana|barra azul"),
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
    (
        UTILITY,
        r"proteger (os |meus |o |meu |minha |a )?(aliados|time|atirador|suporte)|ajudar|suporte|"
        r"peel|aura",
    ),
    (UTILITY, r"habilidades? mais vezes|habilidades (demoram|voltarem)|girar|skills?|ultimate"),
    (
        UTILITY,
        r"\bult\b|mais habilidades|usar (as |minhas )?habilidades|rodar (o mapa)?|outras rotas",
    ),
    (UTILITY, r"correr|andar mais|rotac|chegar (antes|primeiro)|chego (atrasado|tarde)|\butil"),
    (UTILITY, r"\bmana\b|cooldown|\bcdr\b|pelo time|\bvelocidade\b"),
    (SURVIVAL, r"aguent|sobreviv|\bmorr|\bcaio\b|\bcair\b|tank|resist|armadura|\bvida\b"),
    (SURVIVAL, r"defe(sa|nsiv|nder)|protec|seguranca|\bvivo\b|durar|focad|burst|one ?shot"),
    (DAMAGE, r"\bdano\b|\bdps\b|\bmata(r)?\b|\bmato\b|critic|derret|explodir|estourar|abate"),
    (
        DAMAGE,
        r"ofensiv|bat(er|o)\b|estrago|agressiv|atac(ar|ando|o)\b|ataques|doer|doessem|causa\w*",
    ),
)
BALANCED_PATTERN = (
    r"equilibr|balance|um pouco de tudo|tanto faz|padrao|sem preferencia|nada em especial|"
    r"meio termo|neutr|normal|comum|todo mundo faz|tradicional|de sempre|usual|distribu|dividido|"
    r"sem prioridade|nao quero priorizar|nao tenho preferencia|um pouco de cada|costuma|"
    r"nada (em )?especific|divid\w* igual|igual entre"
)
INJECTION_PATTERN = (
    r"ignor[ae] (as|tudo|o|a|suas) |esquec[ae] (as|tudo|suas) |regras|instruc|prompt|sistema|"
    r"system|json|yaml|xml|admin|execut|comando|imprim|variave|chave|senha|finja|voce (agora )?e|"
    r"responda|retorne|devolva|override|priority|needs_input|apague|banco de dados|[{}<>#=]|"
    r"\.env|rm -rf|traduz[ai]|revel|desativ|validacao|duas respostas|ultimo usuario|"
    r"classifi\w*|marqu?e |marca |output|nova tarefa|finge|nota (para|pra|pro) (a ia|o modelo)|"
    r"obs (pro|para o) (modelo|sistema)|sua resposta|nao pense|formato|escrev\w* (so|apenas)|"
    r"modo desenvolvedor|ignore (the|all|todas|isso)|answer|/etc|cat /|desconsidere o que"
)
_STOP = r"(?=[,;.]|\s(quero|preciso|prefiro|o negocio|so|mas|e)\s|$)"
# Gatilho antes do trecho negado ("não quero X"): o próprio gatilho não nega o que começa
# nele ("não morrer" é sobreviver).
TRIGGER_SPANS = (
    r"(nao|nem|sem|nada de|esquece|chega de|tudo menos|menos|evit\w*|ignor\w*|desconsider\w*|"
    r"deixa de lado|pode esquecer|(?<!ao )mesmo)\s.*?" + _STOP,
    r"nao e .+? que eu quero",
)
# Trecho anterior ao marcador ("X não importa agora", "X seria bom"): tudo nele é negado ou adiado.
PREFIX_SPANS = (
    r"(^|[,;.]\s?)[^,;.]*?\snao importa( agora)?",
    r"(^|[,;.]\s?)[^,;.]*?\s(seria bom|fica pra depois|eu ja consigo)",
    # v2: negação depois do termo ("dano não, obrigado", "dano eu não preciso", "vida já tenho")
    r"(^|[,;.]\s?)[^,;.]*?\s(nao( obrigad\w*)?|(eu )?nao preciso( agora)?|"
    r"(eu )?ja tenho( o suficiente| de sobra)?|tenho de sobra|nao e o problema|"
    r"ja (ta|esta) (ok|bom)|ja e suficiente)(?=[,;.]|$)",
)
PRECEDENCE = (
    r"principalmente|mais importante (pra mim )?e|prioridade (e|:)|fico com|acima de tudo|"
    r"o que (me )?falta e|o negocio e|prefiro|o foco e|o problema e|(preciso|quero) e\b|"
    r"mais que tudo|\bfalta\b"
)
# v2: precedência posposta ("X é o que importa"): vale o trecho antes do marcador.
SUFFIX_PRECEDENCE = r"([^,;.]*?)\s(e|eh) (o que (mais )?importa|o principal|o mais importante)"


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
    suffix = re.search(SUFFIX_PRECEDENCE, norm)
    if suffix:
        affirmed, _ = _classes(suffix.group(1))
        if len(affirmed) == 1:
            return affirmed.pop()
    markers = list(re.finditer(PRECEDENCE, norm))
    if markers:
        focus = re.split(
            r"[,;.]| mesmo | mas | do que | ao inves de | em vez de ",
            norm[markers[-1].end() :].strip(),
        )[0]
        affirmed, _ = _classes(focus)
        if len(affirmed) == 1:
            return affirmed.pop()
    affirmed, _ = _classes(norm)
    balanced = bool(re.search(BALANCED_PATTERN, norm))
    if balanced and (not affirmed or re.search(r"\bnem\b|divid|igual entre", norm)):
        return BALANCED
    if len(affirmed) == 1:
        return affirmed.pop()
    return NEEDS_INPUT
