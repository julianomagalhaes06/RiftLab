#!/usr/bin/env python3
"""CC-703: cronometra UMA configuração fixa do ranker (depth 6, lr 0,05, l2 10, 300 iterações sem
parada antecipada) no dataset de compra, para comparar esta máquina com o Colab (CPU e GPU).

Uso: python bench_train.py DATASET.json CHAMPIONS.json ITEMS.json CPU|GPU THREADS
Imprime uma linha JSON. Não grava nada.
"""

from __future__ import annotations

import json
import os
import platform
import sys
import time
from pathlib import Path

from riftlab.ml.features import Catalogs
from riftlab.ml.ranker import _pool


def main(dataset: Path, champions: Path, items: Path, task_type: str, threads: int) -> None:
    from catboost import CatBoostRanker, __version__

    rows = json.loads(dataset.read_bytes())["rows"]
    catalogs = Catalogs.from_data_dragon(json.loads(champions.read_bytes()), json.loads(items.read_bytes()))
    train = [r for r in rows if r["split"] == "train" and r.get("candidates") and r.get("targetLegal")]
    validation = [r for r in rows if r["split"] == "validation"]
    t0 = time.perf_counter()
    train_pool, val_pool = _pool(train, catalogs, frozenset()), _pool(validation, catalogs, frozenset())
    pools = time.perf_counter() - t0
    model = CatBoostRanker(
        loss_function="PairLogit", iterations=300, depth=6, learning_rate=0.05, l2_leaf_reg=10.0,
        random_seed=402, thread_count=threads, task_type=task_type, verbose=False,
        allow_writing_files=False,
    )
    t1 = time.perf_counter()
    model.fit(train_pool, eval_set=val_pool)
    fit = time.perf_counter() - t1
    print(json.dumps({
        "taskType": task_type, "threads": threads, "trainDecisions": len(train),
        "trainRows": train_pool.num_row(), "poolSeconds": round(pools, 1), "fitSeconds": round(fit, 1),
        "secondsPer100Iterations": round(fit / 3, 1), "catboost": __version__,
        "machine": platform.machine(), "cpus": os.cpu_count(),
    }))


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4], int(sys.argv[5]))
