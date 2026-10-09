"""CC-404: validity barrier, support score and abstention for the active B0 ranking.

Support is the number of training decisions in the (champion, main item ordinal)
cell — a count, never a probability. A ranking is returned only for a valid state
with enough support; otherwise INSUFFICIENT_EVIDENCE with readable reasons and no
items. The threshold is chosen on validation only (rule fixed below, before any
look at the results) and stored in a versioned file.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from riftlab.dataset.popularity import PopularityModel
from riftlab.ml.metrics import rank_of

THRESHOLDS = (0, 5, 10, 20, 40)
MAX_RISK = 0.15  # 1 - hit@3 entre as decisões cobertas, na validação
REASON_TEXT = {
    "INVALID_STATE": "O inventário antes desta compra não pôde ser reconstruído com segurança.",
    "NO_LEGAL_CANDIDATES": "Nenhum item principal legal estava disponível neste momento.",
    "PATCH_UNSUPPORTED": "Não há regras de itens nem dados de referência para este patch.",
    "UNKNOWN_CHAMPION": "Não há decisões de treino com este campeão.",
    "LOW_SUPPORT": "Poucas decisões de treino com este campeão nesta etapa da build.",
}


@dataclass(frozen=True, slots=True)
class SupportPolicy:
    patch: str
    threshold: int
    cell_counts: Mapping[tuple[int, int | None], int]

    def decide(self, row: Mapping[str, Any]) -> tuple[bool, list[str]]:
        """(ranked?, reason codes). Validity first, then support."""
        reasons: list[str] = []
        if row.get("patch", self.patch) != self.patch:
            return False, ["PATCH_UNSUPPORTED"]
        if row.get("status") not in (None, "OK", "ZERO_CANDIDATES"):
            reasons.append("INVALID_STATE")
        if not (row.get("candidates") or row.get("candidateItemIds")):
            reasons.append("NO_LEGAL_CANDIDATES")
        if reasons:
            return False, reasons
        champion_seen = any(c == row["championId"] for c, _ in self.cell_counts)
        if not champion_seen:
            return False, ["UNKNOWN_CHAMPION"]
        if self.cell_counts.get((row["championId"], row["mainItemOrdinal"]), 0) < self.threshold:
            return False, ["LOW_SUPPORT"]
        return True, []


def cell_counts(train: Sequence[Mapping[str, Any]]) -> dict[tuple[int, int | None], int]:
    counts: Counter[tuple[int, int | None]] = Counter()
    for row in train:
        if row.get("targetLegal"):
            counts[(row["championId"], row["mainItemOrdinal"])] += 1
    return dict(counts)


def risk_coverage(
    rows: Sequence[Mapping[str, Any]], model: PopularityModel, policy: SupportPolicy
) -> dict[str, Any]:
    """Coverage and error (1 - hit@3) reported together, plus reason counts."""
    covered = hits = 0
    reasons: Counter[str] = Counter()
    evaluable = [r for r in rows if r.get("targetLegal")]
    for row in evaluable:
        ranked, why = policy.decide(row)
        reasons.update(why)
        if not ranked:
            continue
        covered += 1
        candidates = [c["itemId"] for c in row["candidates"]]
        scores = [model.score(row["championId"], row["mainItemOrdinal"], i) for i in candidates]
        hits += rank_of(row["targetItemId"], candidates, scores) <= 3
    n = len(evaluable)
    return {
        "threshold": policy.threshold,
        "decisions": n,
        "covered": covered,
        "coverage": covered / n if n else None,
        "risk": 1 - hits / covered if covered else None,
        "abstentionReasons": dict(sorted(reasons.items())),
    }


def select_threshold(
    validation: Sequence[Mapping[str, Any]],
    model: PopularityModel,
    counts: Mapping[Any, int],
    patch: str,
) -> tuple[int, list[dict[str, Any]]]:
    """Largest coverage with validation risk <= MAX_RISK (ties: smaller threshold)."""
    curve = [risk_coverage(validation, model, SupportPolicy(patch, t, counts)) for t in THRESHOLDS]
    ok = [c for c in curve if c["risk"] is not None and c["risk"] <= MAX_RISK]
    chosen = max(ok, key=lambda c: (c["coverage"], -c["threshold"])) if ok else curve[-1]
    return int(chosen["threshold"]), curve
