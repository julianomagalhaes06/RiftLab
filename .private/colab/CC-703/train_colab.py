#!/usr/bin/env python3
"""CC-703 no Colab: as três execuções do protocolo (compra, planejamento e contexto rico), cada
uma com seleção só na validação e o teste UMA vez, com o mesmo código do projeto (riftlab).

Retomável: uma execução com run.json e test.json é pulada; com run.json sem test.json, só roda o
teste; sem nada, treina e testa. Saídas em OUT/<execução>/ (run.json, test.json, modelos .cbm) e
OUT/timings.jsonl.

Uso: python train_colab.py BUNDLE_DIR OUT_DIR GPU|CPU THREADS
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from riftlab.ml.experiment import test_run, train_run

RUNS = (
    ("purchase", "dataset-v4-features.json"),
    ("planning", "dataset-v4-planning.json"),
    ("planning-ctx", "dataset-v4-planning-ctx.json"),
)


def main(bundle: Path, out: Path, task_type: str, threads: int) -> None:
    champions = bundle / "catalog" / "champion-16.19.1-en_US.json"
    items = bundle / "catalog" / "item-16.19.1-en_US.json"
    out.mkdir(parents=True, exist_ok=True)
    for name, dataset_file in RUNS:
        run_dir = out / f"ranker-16.19-v4-{name}"
        dataset = bundle / "data" / dataset_file
        if (run_dir / "test.json").exists():
            print(f"{name}: já concluída, pulando", flush=True)
            continue
        started = time.time()
        if not (run_dir / "run.json").exists():
            if run_dir.exists():
                raise SystemExit(
                    f"{run_dir} existe sem run.json (treino interrompido). Renomeie-a para "
                    "<nome>-interrompida e rode de novo; nada é sobrescrito."
                )
            print(f"{name}: treinando ({task_type}, {threads} threads)…", flush=True)
            report = train_run(dataset, champions, items, run_dir, task_type=task_type, thread_count=threads)
            print(f"{name}: validação", {m: round(r["all"]["point"]["mrr"], 3) for m, r in report["validation"].items()}, flush=True)
        trained = time.time()
        result = test_run(run_dir, dataset, champions, items)
        print(f"{name}: teste", {m: round(r["all"]["point"]["mrr"], 3) for m, r in result["test"].items()}, flush=True)
        with (out / "timings.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"run": name, "taskType": task_type, "threads": threads,
                                     "trainSeconds": round(trained - started, 1),
                                     "testSeconds": round(time.time() - trained, 1)}) + "\n")


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3], int(sys.argv[4]))
