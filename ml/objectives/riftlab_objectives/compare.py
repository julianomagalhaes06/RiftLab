"""CC-503: tabela comparativa a partir dos summary.json das avaliações.

Uso: uv run python -m riftlab_objectives.compare [--reference RUN] RUN [RUN ...]
Cada RUN é um diretório em artifacts/objectives/. Gate do card (registrado antes do teste):
JSON válido ≥ 0,95, intenção (macro-F1) ≥ 0,90 e nenhuma regressão relevante de ambiguidade —
recall de NEEDS_INPUT no máximo 0,05 abaixo do sistema de referência (o provedor de regras,
padrão atual). Avaliado, nunca presumido.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3] / "artifacts" / "objectives"
GATE_JSON, GATE_F1, GATE_AMBIGUITY_DROP = 0.95, 0.90, 0.05


def load(run: str) -> dict:
    return json.loads((ROOT / run / "summary.json").read_text())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference", default="")
    parser.add_argument("runs", nargs="+")
    args = parser.parse_args()
    reference = None
    if args.reference:
        reference = load(args.reference)["perClass"]["NEEDS_INPUT"]["recall"]
    print("| Sistema | Split | N | JSON válido | Acurácia | Macro-F1 | NEEDS_INPUT P/R | Negação "
          "| p50 (s) | p95 (s) | Gate |")
    print("| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |")
    for run in args.runs:
        s = load(run)
        ask = s["perClass"]["NEEDS_INPUT"]
        failed = []
        if s["jsonValidRate"] < GATE_JSON:
            failed.append("JSON")
        if s["macroF1"] < GATE_F1:
            failed.append("F1")
        if reference is not None and ask["recall"] < reference - GATE_AMBIGUITY_DROP:
            failed.append("ambiguidade")
        gate = "aprovado" if not failed else "reprovado (" + ", ".join(failed) + ")"
        cells = [run, s["split"], s["n"], f"{s['jsonValidRate']:.3f}", f"{s['accuracy']:.3f}",
                 f"{s['macroF1']:.3f}", f"{ask['precision']:.2f}/{ask['recall']:.2f}",
                 f"{s['negationAccuracy']:.3f}", f"{s['latencyP50S']:.2f}",
                 f"{s['latencyP95S']:.2f}", gate]
        print("| " + " | ".join(str(c) for c in cells) + " |")


if __name__ == "__main__":
    main()
