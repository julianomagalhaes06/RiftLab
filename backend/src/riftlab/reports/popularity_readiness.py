"""CC-803 parte B: prontidão da popularidade própria de um patch contra a herdada filtrada.

Puro (sem banco): recebe as linhas do dataset do patch, a referência própria (células com
o mínimo de decisões de treino) e a herdada já filtrada pelo catálogo. A ordem de cada
decisão é a do critério servido (`ranking.engine.Popularity.score`, desempate por item):
a proposta usa a própria quando a célula tem suporte e a herdada nas demais, como a análise
faz. Mede imitação da escolha histórica, não a qualidade da compra.
"""

from __future__ import annotations

import random
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from riftlab.ranking.engine import DecisionFacts, Popularity

BOOTSTRAP_RESAMPLES = 2000
BOOTSTRAP_SEED = 401


def cell_support(rows: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    """Decisões de TREINO com alvo legal por célula campeão|ordem."""
    counts: Counter[str] = Counter()
    for row in rows:
        if row["split"] == "train" and row.get("targetLegal"):
            counts[f"{row['championId']}|{row['mainItemOrdinal']}"] += 1
    return dict(sorted(counts.items()))


def _position(
    popularity: Popularity, decision: DecisionFacts, candidates: Sequence[int], target: int
) -> int:
    order = sorted(candidates, key=lambda item: (-popularity.score(decision, item), item))
    return order.index(target) + 1


def _uniform(size: int) -> tuple[float, float]:
    """MRR e hit@1 esperados de uma ordem uniforme sobre `size` candidatos (exatos)."""
    return sum(1 / k for k in range(1, size + 1)) / size, 1 / size


def evaluate_split(
    rows: Iterable[Mapping[str, Any]],
    split: str,
    own: Popularity | None,
    inherited: Popularity | None,
) -> dict[str, Any]:
    """Métricas por decisão na parte pedida; nada é arredondado."""
    per_match: dict[str, list[tuple[float, float, float, float, float, float]]] = defaultdict(list)
    counts: Counter[str] = Counter()
    by_cell: dict[str, Counter[str]] = defaultdict(Counter)
    for row in rows:
        if row["split"] != split:
            continue
        counts["rows"] += 1
        candidates = [int(item) for item in row.get("candidateItemIds") or []]
        if not candidates or not row.get("targetLegal"):
            counts["notEvaluable"] += 1
            continue
        decision = DecisionFacts(int(row["championId"]), int(row["mainItemOrdinal"]), None, None)
        cell = f"{decision.champion_id}|{decision.main_item_ordinal}"
        target = int(row["targetItemId"])
        own_supported = own is not None and own.support(decision) > 0
        inherited_supported = inherited is not None and inherited.support(decision) > 0
        if not inherited_supported and not own_supported:
            counts["withoutAnyReference"] += 1
            continue
        if not inherited_supported:
            # Sem referência herdada a comparação pareada não existe para a decisão.
            counts["withoutInheritedReference"] += 1
            continue
        assert inherited is not None
        proposal = own if own_supported else inherited
        assert proposal is not None
        p_pos = _position(proposal, decision, candidates, target)
        i_pos = _position(inherited, decision, candidates, target)
        u_mrr, u_hit = _uniform(len(candidates))
        counts["evaluated"] += 1
        counts["usingOwn" if own_supported else "usingInheritedFallback"] += 1
        by_cell[cell]["evaluated"] += 1
        by_cell[cell]["usingOwn"] += int(own_supported)
        per_match[str(row["matchId"])].append(
            (1 / p_pos, float(p_pos == 1), 1 / i_pos, float(i_pos == 1), u_mrr, u_hit)
        )
    values = [value for match in per_match.values() for value in match]
    n = len(values)

    def mean(index: int) -> float | None:
        return sum(v[index] for v in values) / n if n else None

    return {
        "split": split,
        "counts": dict(sorted(counts.items())),
        "evaluatedMatches": len(per_match),
        "byCell": {cell: dict(c) for cell, c in sorted(by_cell.items())},
        "proposal": {"mrr": mean(0), "hitAt1": mean(1)},
        "inheritedFiltered": {"mrr": mean(2), "hitAt1": mean(3)},
        "uniformExpected": {"mrr": mean(4), "hitAt1": mean(5)},
        "pairedBootstrapDeltaMrr": _bootstrap(per_match),
        "measures": "IMITATION_OF_HISTORICAL_CHOICE",
    }


def _bootstrap(per_match: Mapping[str, Sequence[tuple[float, ...]]]) -> dict[str, Any] | None:
    """Delta MRR (proposta − herdada), reamostrando partidas; informativo, não decide."""
    matches = sorted(per_match)
    if len(matches) < 2:
        return None
    rng = random.Random(BOOTSTRAP_SEED)
    deltas = []
    for _ in range(BOOTSTRAP_RESAMPLES):
        sample = [per_match[rng.choice(matches)] for _ in matches]
        values = [v for match in sample for v in match]
        deltas.append(sum(v[0] - v[2] for v in values) / len(values))
    deltas.sort()
    low = deltas[int(0.025 * BOOTSTRAP_RESAMPLES)]
    high = deltas[int(0.975 * BOOTSTRAP_RESAMPLES) - 1]
    return {"resamples": BOOTSTRAP_RESAMPLES, "seed": BOOTSTRAP_SEED, "ci95": [low, high]}


def selection(validation: Mapping[str, Any], cells_with_minimum: int) -> dict[str, str]:
    """Regra pré-registrada: troca só se MRR e hit@1 da proposta >= herdada; empate mantém
    a própria; sem célula com mínimo ou sem decisão avaliada, NO_SAMPLE."""
    proposal, inherited = validation["proposal"], validation["inheritedFiltered"]
    if not cells_with_minimum or proposal["mrr"] is None:
        return {"decision": "NO_SAMPLE", "keep": "INHERITED"}
    if proposal["mrr"] >= inherited["mrr"] and proposal["hitAt1"] >= inherited["hitAt1"]:
        return {"decision": "ADOPT_OWN", "keep": "OWN_WITH_INHERITED_FALLBACK"}
    return {"decision": "KEEP_INHERITED", "keep": "INHERITED"}
