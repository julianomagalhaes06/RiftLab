"""CC-503: avalia o provedor RULES do backend no mesmo formato do evaluate.py.

Uso: uv run python -m riftlab_objectives.evaluate_rules --split val|test
Importa backend/src/riftlab/objectives/rules.py sem instalar o backend (módulo puro).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

from riftlab_objectives.metrics import summarise

ROOT = Path(__file__).resolve().parents[3]
CORPUS = Path(__file__).resolve().parents[1] / "corpus"
sys.path.insert(0, str(ROOT / "backend" / "src"))

from riftlab.objectives.rules import RULES_VERSION, interpret  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--split", required=True, choices=["train", "val", "test"])
    args = parser.parse_args()
    path = CORPUS / f"{args.split}-v1.jsonl"
    rows = [json.loads(line) for line in path.read_text("utf-8").splitlines()]
    for row in rows:
        t0 = time.perf_counter()
        row["pred"] = interpret(row["text"])
        row["latencyS"] = round(time.perf_counter() - t0, 6)
        row["raw"] = json.dumps({"priority": row["pred"]})
        row["jsonValid"] = True  # saída construída pelo código, não gerada
    summary = summarise(rows) | {"config": {"provider": "RULES", "version": RULES_VERSION},
                                 "split": args.split,
                                 "corpusSha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    out = ROOT / "artifacts" / "objectives" / f"rules-{args.split}"
    out.mkdir(parents=True, exist_ok=True)
    (out / "predictions.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k: summary[k] for k in ("n", "macroF1", "accuracy", "negationAccuracy")}))


if __name__ == "__main__":
    main()
