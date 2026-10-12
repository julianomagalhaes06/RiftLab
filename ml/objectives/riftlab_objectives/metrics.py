"""Métricas do interpretador: macro-F1 nas 5 classes, acurácia, JSON válido, latência."""

from __future__ import annotations

from collections import Counter, defaultdict

from riftlab_objectives.prompt import LABELS


def macro_f1(gold: list[str], pred: list[str]) -> tuple[float, dict[str, dict[str, float]]]:
    per: dict[str, dict[str, float]] = {}
    for label in LABELS:
        tp = sum(g == p == label for g, p in zip(gold, pred, strict=True))
        fp = sum(p == label and g != label for g, p in zip(gold, pred, strict=True))
        fn = sum(g == label and p != label for g, p in zip(gold, pred, strict=True))
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        per[label] = {"precision": round(precision, 4), "recall": round(recall, 4),
                      "f1": round(f1, 4), "support": tp + fn}
    return round(sum(v["f1"] for v in per.values()) / len(LABELS), 4), per


def percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(q * (len(ordered) - 1))))
    return round(ordered[index], 4)


def summarise(rows: list[dict]) -> dict:
    gold = [r["label"] for r in rows]
    pred = [r["pred"] for r in rows]
    f1, per = macro_f1(gold, pred)
    by_cat: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        by_cat[r["category"]]["n"] += 1
        by_cat[r["category"]]["ok"] += r["pred"] == r["label"]
    latency = [r["latencyS"] for r in rows]
    return {
        "n": len(rows),
        "macroF1": f1,
        "accuracy": round(sum(g == p for g, p in zip(gold, pred, strict=True)) / len(rows), 4),
        "jsonValidRate": round(sum(r["jsonValid"] for r in rows) / len(rows), 4),
        "perClass": per,
        "perCategoryAccuracy": {c: round(v["ok"] / v["n"], 4) for c, v in sorted(by_cat.items())},
        "negationAccuracy": round(
            sum(r["pred"] == r["label"] for r in rows if r.get("negation"))
            / max(1, sum(bool(r.get("negation")) for r in rows)), 4),
        "latencyP50S": percentile(latency, 0.5),
        "latencyP95S": percentile(latency, 0.95),
    }
