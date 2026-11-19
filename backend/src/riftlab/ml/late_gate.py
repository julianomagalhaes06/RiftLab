"""Decisão do gate do late game (CC-1503) a partir do relatório do teste único. Puro.

Regras do pré-registro `validation/sprint-15/CC-1503/01-pre-registro.md`:
- G1: Δ log-perda (modelo − padrão) em todas as decisões, limite superior do IC 95% < 0.
- G2: Δ acerto@3 nos desvios, limite inferior do IC 95% > 0.
- G3: no 5º+ item, Δ acerto@3 pontual ≥ −0,01 e Δ log-perda pontual < 0.
Frase forte do desvio: taxa observada ≥ 1,25 × a esperada pelo padrão.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def decide_gate(report: Mapping[str, Any]) -> dict[str, Any]:
    all_ = report["all"]
    deviation = report["deviation"]
    late = report["ordinal:5+"]
    g1 = all_["ci95"]["delta_nll"][1] < 0
    g2 = deviation["ci95"]["delta_hit3"][0] > 0
    point = late["point"]
    g3 = (point["model_hit3"] - point["base_hit3"]) >= -0.01 and (
        point["model_nll"] - point["base_nll"]
    ) < 0
    claims = report.get("deviationClaims") or {}
    observed, expected = claims.get("observedRate"), claims.get("expectedByBase")
    strong = bool(observed is not None and expected and observed >= 1.25 * expected)
    return {
        "G1": g1,
        "G2": g2,
        "G3": g3,
        "passed": bool(g1 and g2 and g3),
        "claimStrong": strong,
        "preregistration": "validation/sprint-15/CC-1503/01-pre-registro.md",
    }


__all__ = ["decide_gate"]
