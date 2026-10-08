"""CC-402: run the comparison protocol — selection on validation, then a single test run."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from riftlab.dataset.popularity import fit
from riftlab.ml.features import ABLATIONS, Catalogs
from riftlab.ml.metrics import evaluate
from riftlab.ml.ranker import GRID, b0_scores, b1_score, model_scores, train_and_select

RESAMPLES, SEED = 2000, 401


class ExperimentError(RuntimeError):
    """Protocol violation or missing input; nothing is written."""


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_once(path: Path, payload: Mapping[str, Any], mode: int = 0o600) -> str:
    raw = (json.dumps(payload, indent=1, sort_keys=True, default=str) + "\n").encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        os.write(fd, raw)
    finally:
        os.close(fd)
    return hashlib.sha256(raw).hexdigest()


def _load(
    dataset_path: Path, champion_path: Path, item_path: Path
) -> tuple[list[dict[str, Any]], Catalogs]:
    artifact = json.loads(dataset_path.read_bytes())
    rows = artifact["rows"]
    if not rows or "candidates" not in rows[0]:
        raise ExperimentError("dataset sem candidatos enriquecidos (custo restante e features)")
    catalogs = Catalogs.from_data_dragon(
        json.loads(champion_path.read_bytes()), json.loads(item_path.read_bytes())
    )
    return rows, catalogs


def train_run(
    dataset_path: Path, champion_path: Path, item_path: Path, run_dir: Path
) -> dict[str, Any]:
    if run_dir.exists():
        raise ExperimentError("diretório da execução já existe; nunca sobrescrito")
    rows, catalogs = _load(dataset_path, champion_path, item_path)
    train = [r for r in rows if r["split"] == "train"]
    validation = [r for r in rows if r["split"] == "validation"]
    run_dir.mkdir(parents=True, mode=0o700)
    import catboost
    import numpy

    popularity = fit(train)
    report: dict[str, Any] = {
        "schemaVersion": 1,
        "card": "CC-402",
        "inputs": {
            "datasetSha256": _sha(dataset_path),
            "championCatalogSha256": _sha(champion_path),
            "itemCatalogSha256": _sha(item_path),
        },
        "environment": {
            "python": sys.version.split()[0],
            "catboost": catboost.__version__,
            "numpy": numpy.__version__,
            "machine": platform.machine(),
            "threads": 1,
        },
        "grid": list(GRID),
        "validation": {
            "B0": evaluate(validation, b0_scores(popularity), resamples=RESAMPLES, seed=SEED),
            "B1": evaluate(validation, b1_score, resamples=RESAMPLES, seed=SEED),
        },
        "models": {},
    }
    for name, drop in ABLATIONS.items():
        selected = train_and_select(train, validation, catalogs, drop)
        model_path = run_dir / f"m1-{name}.cbm"
        selected["model"].save_model(str(model_path))
        report["validation"][f"M1-{name}"] = evaluate(
            validation,
            model_scores(selected["model"], catalogs, drop),
            resamples=RESAMPLES,
            seed=SEED,
        )
        report["models"][f"M1-{name}"] = {
            "file": model_path.name,
            "sha256": _sha(model_path),
            "dropped": sorted(drop),
            "features": list(selected["model"].feature_names_),
            "config": selected["config"],
            "grid": selected["grid"],
            "trainSeconds": selected["trainSeconds"],
            "maxRssMb": selected["maxRssMb"],
        }
    report["runSha256"] = _write_once(run_dir / "run.json", report)
    return report


def test_run(
    run_dir: Path, dataset_path: Path, champion_path: Path, item_path: Path
) -> dict[str, Any]:
    """Single evaluation on the test split; refuses a second run or changed inputs."""
    from catboost import CatBoostRanker

    run = json.loads((run_dir / "run.json").read_bytes())
    if (run_dir / "test.json").exists():
        raise ExperimentError("teste já executado; nunca reexecutado")
    if run["inputs"]["datasetSha256"] != _sha(dataset_path):
        raise ExperimentError("dataset diverge do usado na seleção")
    rows, catalogs = _load(dataset_path, champion_path, item_path)
    train = [r for r in rows if r["split"] == "train"]
    test = [r for r in rows if r["split"] == "test"]
    popularity = fit(train)
    result: dict[str, Any] = {
        "schemaVersion": 1,
        "card": "CC-402",
        "runSha256": _sha(run_dir / "run.json"),
        "test": {
            "B0": evaluate(test, b0_scores(popularity), resamples=RESAMPLES, seed=SEED),
            "B1": evaluate(test, b1_score, resamples=RESAMPLES, seed=SEED),
        },
    }
    for name, meta in run["models"].items():
        if _sha(run_dir / meta["file"]) != meta["sha256"]:
            raise ExperimentError("modelo alterado depois da seleção")
        model = CatBoostRanker()
        model.load_model(str(run_dir / meta["file"]))
        result["test"][name] = evaluate(
            test,
            model_scores(model, catalogs, frozenset(meta["dropped"])),
            resamples=RESAMPLES,
            seed=SEED,
        )
    _write_once(run_dir / "test.json", result, mode=0o400)
    return result
