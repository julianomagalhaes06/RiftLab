"""Avaliação do late game (CC-1503, P5): medir o que não é óbvio.

O MRR global é dominado pelo padrão. Aqui cada decisão entra em fatias: ordem do item, tipo da
compra no instante em que ficou pronta (FORCED/SHARED/OPEN) e, principalmente, DESVIO: o alvo não
é o item mais provável do padrão. Num desvio o padrão erra o 1º lugar por definição; a pergunta é
se o contexto o coloca entre os 3 primeiros.

`deviation_claims` mede a frase que a tela vai dizer ("esta partida pede X"): quando o modelo
aponta um desvio, com que frequência o jogador comprou X, comparado com o que o padrão esperava.

Intervalos: bootstrap de partidas (a unidade independente), 2.000 reamostragens, semente fixa.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

EPS = 1e-4
LIFT_THRESHOLD = math.log(1.5)
MIN_DEVIATION_P = 0.05
RESAMPLES = 2000
SEED = 1503


@dataclass(frozen=True, slots=True)
class Scored:
    """Uma decisão avaliada: candidatos plausíveis, P do padrão e P do modelo."""

    row: Mapping[str, Any]
    ids: Sequence[int]
    base: np.ndarray
    model: np.ndarray


def _rank(ids: Sequence[int], p: np.ndarray, target: int) -> float:
    if target not in ids:
        return math.inf
    order = sorted(range(len(ids)), key=lambda i: (-p[i], ids[i]))
    return float(order.index(list(ids).index(target)) + 1)


def per_decision(scored: Scored) -> dict[str, float]:
    target = int(scored.row["targetItemId"])
    out: dict[str, float] = {}
    for name, p in (("base", scored.base), ("model", scored.model)):
        rank = _rank(scored.ids, p, target)
        prob = float(p[list(scored.ids).index(target)]) if target in scored.ids else 0.0
        out[f"{name}_hit1"] = float(rank <= 1)
        out[f"{name}_hit3"] = float(rank <= 3)
        out[f"{name}_rr"] = 0.0 if math.isinf(rank) else 1.0 / rank
        out[f"{name}_nll"] = -math.log(max(prob, EPS))
    return out


def deviation_item(scored: Scored) -> int | None:
    """O desvio que a tela mostraria: o item de maior P do modelo, fora do 1º do padrão, com
    lift ≥ log(1,5) e P ≥ 5%."""
    ids = list(scored.ids)
    if not ids:
        return None
    default = ids[int(np.argmax(scored.base))]
    best: tuple[float, int] | None = None
    for i, item in enumerate(ids):
        if item == default:
            continue
        p, q = float(scored.model[i]), float(scored.base[i])
        if p >= MIN_DEVIATION_P and math.log(p / max(q, 1e-12)) >= LIFT_THRESHOLD:
            if best is None or p > best[0]:
                best = (p, item)
    return best[1] if best else None


def slices(row: Mapping[str, Any], scored: Scored) -> list[str]:
    target = int(row["targetItemId"])
    ids = list(scored.ids)
    default = ids[int(np.argmax(scored.base))] if ids else None
    kind = (row.get("completion") or {}).get("kind", "UNKNOWN")
    names = ["all", f"ordinal:{row['ordinal']}", f"completion:{kind}", f"role:{row.get('role')}"]
    if default is not None and target != default:
        names.append("deviation")
        names.append(f"deviation:ordinal:{row['ordinal']}")
    if int(row["ordinal"]) >= 5:
        names.append("ordinal:5+")
        if default is not None and target != default:
            names.append("deviation:ordinal:5+")
    if not row.get("targetLegal"):
        names.append("target_illegal")
    elo = row.get("elo")
    if elo:  # faixa de elo do corpus (descritivo; o gate usa só os recortes acima)
        names.append(f"elo:{elo}")
        if int(row["ordinal"]) >= 5:
            names.append(f"elo:{elo}:ordinal:5+")
    return names


def _bootstrap(
    values: Mapping[str, list[float]], match_of: Sequence[str], rng: np.random.Generator
) -> dict[str, list[float]]:
    matches = sorted(set(match_of))
    index = {m: i for i, m in enumerate(matches)}
    sums: dict[str, np.ndarray] = {}
    counts = np.zeros(len(matches))
    for m in match_of:
        counts[index[m]] += 1
    for name, series in values.items():
        acc = np.zeros(len(matches))
        for i, m in enumerate(match_of):
            acc[index[m]] += series[i]
        sums[name] = acc
    draws: dict[str, list[float]] = defaultdict(list)
    for _ in range(RESAMPLES):
        pick = rng.integers(0, len(matches), len(matches))
        weight = np.bincount(pick, minlength=len(matches))
        n = float(weight @ counts)
        if n == 0:
            continue
        for name, acc in sums.items():
            draws[name].append(float(weight @ acc) / n)
    return {
        name: [float(np.percentile(d, 2.5)), float(np.percentile(d, 97.5))]
        for name, d in draws.items()
    }


def evaluate(scored: Sequence[Scored], *, resamples: bool = True) -> dict[str, Any]:
    """Métricas por fatia, diferenças modelo − padrão com IC por partida."""
    by_slice: dict[str, list[tuple[str, dict[str, float]]]] = defaultdict(list)
    for item in scored:
        metrics = per_decision(item)
        for name in slices(item.row, item):
            by_slice[name].append((str(item.row["matchId"]), metrics))
    rng = np.random.default_rng(SEED)
    report: dict[str, Any] = {}
    for name in sorted(by_slice):
        entries = by_slice[name]
        keys = entries[0][1].keys()
        point = {k: float(np.mean([m[k] for _, m in entries])) for k in keys}
        result: dict[str, Any] = {
            "decisions": len(entries),
            "matches": len({m for m, _ in entries}),
            "point": point,
        }
        if resamples and len(entries) >= 30:
            deltas = {
                f"delta_{metric}": [m[f"model_{metric}"] - m[f"base_{metric}"] for _, m in entries]
                for metric in ("hit1", "hit3", "rr", "nll")
            }
            result["ci95"] = _bootstrap(deltas, [m for m, _ in entries], rng)
        report[name] = result
    report["deviationClaims"] = deviation_claims(scored)
    return report


def deviation_claims(scored: Sequence[Scored]) -> dict[str, Any]:
    shown = hits = 0
    expected_base = expected_model = 0.0
    by_ordinal: dict[int, list[int]] = defaultdict(lambda: [0, 0])
    for item in scored:
        claim = deviation_item(item)
        if claim is None:
            continue
        i = list(item.ids).index(claim)
        shown += 1
        hit = int(int(item.row["targetItemId"]) == claim)
        hits += hit
        expected_base += float(item.base[i])
        expected_model += float(item.model[i])
        by_ordinal[int(item.row["ordinal"])][0] += 1
        by_ordinal[int(item.row["ordinal"])][1] += hit
    return {
        "decisions": len(scored),
        "shown": shown,
        "shownShare": shown / len(scored) if scored else None,
        "observedRate": hits / shown if shown else None,
        "expectedByBase": expected_base / shown if shown else None,
        "expectedByModel": expected_model / shown if shown else None,
        "byOrdinal": {
            str(k): {"shown": v[0], "observedRate": v[1] / v[0]}
            for k, v in sorted(by_ordinal.items())
        },
    }


def score_rows(
    rows: Sequence[Mapping[str, Any]],
    scorer: Callable[[Mapping[str, Any]], tuple[Sequence[int], np.ndarray, np.ndarray]],
) -> list[Scored]:
    out: list[Scored] = []
    for row in rows:
        if not row.get("candidates"):
            continue
        ids, base, model = scorer(row)
        out.append(Scored(row, ids, base, model))
    return out


__all__ = [
    "LIFT_THRESHOLD",
    "MIN_DEVIATION_P",
    "Scored",
    "deviation_claims",
    "deviation_item",
    "evaluate",
    "per_decision",
    "score_rows",
]
