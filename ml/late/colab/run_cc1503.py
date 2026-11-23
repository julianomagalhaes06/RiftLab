"""CC-1503 no Colab: fumaça, treino do modelo de late game, teste único, gate e revisão cega.

Executado pelo notebook `riftlab-cc1503-treino-colab.ipynb` a partir do pacote auditado. Cada
etapa grava o resultado no registro persistente (Drive) e é pulada quando já concluída:

1. fumaça: recorte de treino e validação (nenhuma linha de teste), 20 árvores; decide o
   dispositivo (GPU; CPU só se a GPU recusar a perda ou o peso por decisão — emenda 03);
2. treino completo (`cc late train`), parâmetros da emenda 02;
3. teste único (`cc late evaluate-test`) com os pré-registros 01, 02 e 03 selados — uma marca
   no registro impede uma segunda abertura, mesmo em outra sessão;
4. gate (`ml.late_gate.decide_gate`) e revisão cega (`cc late review-sample`).

Uso: python run_cc1503.py --root /content/riftlab \
       --journal /content/drive/MyDrive/RiftLab/cc1503/<sha-do-pacote>
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import shutil
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

PATCH = "16.19"
ROWS = ".private/dataset/late-16.19-v2.jsonl.gz"
ITEM_EN = ".private/research/data-dragon/item-16.19.1-en_US.json"
ITEM_PT = ".private/research/data-dragon/item-16.19.1-pt_BR.json"
PREREG = [
    ".private/validation/sprint-15/CC-1503/01-pre-registro.md",
    ".private/validation/sprint-15/CC-1503/02-emenda-pre-registro.md",
    ".private/validation/sprint-15/CC-1503/03-emenda-colab.md",
]
ARTIFACT = ".private/artifacts/late-16.19-v2"
ARTIFACT_FILES = ("model.cbm", "prior.json", "metadata.json", "validation-report.json")
SMOKE_LIMITS = {"train": 1500, "validation": 400}  # nenhuma linha de teste


def log(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def cc(root: Path, journal: Path, name: str, *args: str) -> None:
    """`cc late <args>` num subprocesso, com o log em tempo real e salvo no registro."""
    env = {**os.environ, "PYTHONPATH": str(root / "backend" / "src"), "PYTHONUNBUFFERED": "1"}
    command = [sys.executable, "-c", "from riftlab.cli.late import late_app; late_app()", *args]
    path = journal / f"{name}.log"
    with (
        path.open("a") as output,
        subprocess.Popen(
            command,
            cwd=root / "backend",
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        ) as process,
    ):
        assert process.stdout is not None
        for line in process.stdout:
            print(line, end="", flush=True)
            output.write(line)
            output.flush()
    if process.returncode != 0:
        raise SystemExit(f"etapa {name} falhou (código {process.returncode}); ver {path}")


def smoke(root: Path, journal: Path, task_type: str) -> str:
    done = journal / "smoke.json"
    if done.exists():
        return json.loads(done.read_text())["taskType"]
    folder = root / ".private" / "runtime" / "smoke"
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True)
    counts: Counter[str] = Counter()
    subset = folder / "rows.jsonl.gz"
    with gzip.open(root / ROWS, "rt") as source, gzip.open(subset, "wt") as target:
        for line in source:
            split = json.loads(line)["split"]
            if split in SMOKE_LIMITS and counts[split] < SMOKE_LIMITS[split]:
                counts[split] += 1
                target.write(line)
            if all(counts[k] >= v for k, v in SMOKE_LIMITS.items()):
                break
    chosen = task_type
    for attempt in [task_type, "CPU"] if task_type == "GPU" else [task_type]:
        out = folder / f"artifact-{attempt.lower()}"
        try:
            cc(
                root,
                journal,
                f"smoke-{attempt.lower()}",
                "train",
                "--rows",
                str(subset),
                "--item-json",
                str(root / ITEM_EN),
                "--patch",
                PATCH,
                "--output-dir",
                str(out),
                "--iterations",
                "20",
                "--task-type",
                attempt,
                "--threads",
                "2",
            )
            chosen = attempt
            break
        except SystemExit as exc:
            log(f"fumaça em {attempt} falhou: {exc}")
            if attempt == "CPU" or task_type != "GPU":
                raise
    report = json.loads(
        (folder / f"artifact-{chosen.lower()}" / "validation-report.json").read_text()
    )
    done.write_text(
        json.dumps(
            {
                "taskType": chosen,
                "rows": dict(counts),
                "validationDecisions": report["validation"]["all"]["decisions"],
            }
        )
    )
    log(f"fumaça concluída; dispositivo do treino: {chosen}")
    return chosen


def train(root: Path, journal: Path, task_type: str) -> None:
    saved = journal / "artifact"
    if (saved / "model.cbm").exists() and (saved / "validation-report.json").exists():
        log("treino já concluído neste registro; pulando")
        return
    local = root / ARTIFACT
    shutil.rmtree(local, ignore_errors=True)  # treino interrompido recomeça do zero
    started = time.time()
    cc(
        root,
        journal,
        "train",
        "train",
        "--rows",
        str(root / ROWS),
        "--item-json",
        str(root / ITEM_EN),
        "--patch",
        PATCH,
        "--output-dir",
        str(local),
        "--top-k",
        "30",
        "--depth",
        "6",
        "--learning-rate",
        "0.1",
        "--iterations",
        "1500",
        "--task-type",
        task_type,
        "--threads",
        str(os.cpu_count() or 2),
    )
    saved.mkdir(parents=True, exist_ok=True)
    for name in ARTIFACT_FILES:
        shutil.copy2(local / name, saved / name)
    log(f"treino concluído em {round((time.time() - started) / 60, 1)} min; artefato no registro")


def test_once(root: Path, journal: Path) -> dict:
    report_path = journal / "test-report.json"
    if report_path.exists():
        log("teste já aberto e concluído neste registro; reaproveitando o relatório")
        return json.loads(report_path.read_text())
    if (journal / "TEST_STARTED").exists():
        raise SystemExit(
            "o teste começou numa sessão anterior e não deixou relatório: não reabrir; devolver o "
            "ZIP de resultados para revisão (o test.log mostra até onde foi)"
        )
    local = restore_artifact(root, journal)
    # Marca no Drive ANTES de abrir: se a sessão cair no meio, o teste não é reaberto às cegas.
    (journal / "TEST_STARTED").write_text(json.dumps({"startedAt": time.time()}))
    args = [
        "evaluate-test",
        "--rows",
        str(root / ROWS),
        "--item-json",
        str(root / ITEM_EN),
        "--patch",
        PATCH,
        "--artifact",
        str(local),
    ]
    for document in PREREG:
        args += ["--preregistration", str(root / document)]
    try:
        cc(root, journal, "test", *args)
    finally:
        if (local / "TEST_OPENED").exists():
            shutil.copy2(local / "TEST_OPENED", journal / "TEST_OPENED")
    shutil.copy2(local / "test-report.json", report_path)
    return json.loads(report_path.read_text())


def restore_artifact(root: Path, journal: Path) -> Path:
    """O artefato salvo no registro, de volta ao disco do Colab (sessão nova ou reiniciada)."""
    local = root / ARTIFACT
    local.mkdir(parents=True, exist_ok=True)
    for name in ARTIFACT_FILES:
        if not (local / name).exists():
            shutil.copy2(journal / "artifact" / name, local / name)
    return local


def gate_and_review(root: Path, journal: Path, report: dict) -> None:
    sys.path.insert(0, str(root / "backend" / "src"))
    from riftlab.ml.late_gate import decide_gate

    gate = decide_gate(report)
    (journal / "gate.json").write_text(json.dumps(gate, indent=1, ensure_ascii=False))
    log(f"gate: {gate}")
    review = journal / "revisao-cega-v1"
    if not (review / "gabarito.json").exists():
        restore_artifact(root, journal)
        target = root / ".private" / "validation" / "revisao-cega-v1"
        shutil.rmtree(target, ignore_errors=True)
        cc(
            root,
            journal,
            "review",
            "review-sample",
            "--rows",
            str(root / ROWS),
            "--item-json",
            str(root / ITEM_PT),
            "--patch",
            PATCH,
            "--artifact",
            str(root / ARTIFACT),
            "--output",
            str(target),
        )
        shutil.copytree(target, review, dirs_exist_ok=True)


def summary(journal: Path) -> None:
    report = json.loads((journal / "test-report.json").read_text())
    validation = json.loads((journal / "artifact" / "validation-report.json").read_text())
    lines = {"gate": json.loads((journal / "gate.json").read_text())}
    for name in ("all", "deviation", "ordinal:5+", "deviation:ordinal:5+"):
        if name in report:
            lines[f"teste {name}"] = {
                "decisões": report[name]["decisions"],
                **{k: round(v, 4) for k, v in report[name]["point"].items()},
                "ic95": report[name].get("ci95"),
            }
    lines["frase do desvio (teste)"] = report.get("deviationClaims")
    lines["validação all"] = validation["validation"]["all"]["point"]
    (journal / "resumo.json").write_text(json.dumps(lines, indent=1, ensure_ascii=False))
    print(json.dumps(lines, indent=1, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--task-type", choices=("GPU", "CPU"), default="GPU")
    args = parser.parse_args()
    args.journal.mkdir(parents=True, exist_ok=True)
    device = smoke(args.root, args.journal, args.task_type)
    train(args.root, args.journal, device)
    report = test_once(args.root, args.journal)
    gate_and_review(args.root, args.journal, report)
    summary(args.journal)


if __name__ == "__main__":
    main()
