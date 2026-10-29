"""Executa o gate v3 no Colab; nunca importar modelos em verificações locais deste módulo."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import urlopen

REPOSITORY = "julianomagalhaes06/RiftLab"
GATE_PATH = "configs/objective-parser-gate-v3.yaml"
RUNS = (
    (
        "rules_v1",
        "rules-v3-test",
        ["evaluate_rules", "--corpus", "v3", "--rules", "v1"],
    ),
    (
        "rules_v2",
        "rules-v2-v3-test",
        ["evaluate_rules", "--corpus", "v3", "--rules", "v2"],
    ),
    (
        "qwen2.5-1.5b-lora-v2",
        "lora-1.5b-v2-on-v3-test",
        [
            "evaluate",
            "--config",
            "../../configs/experiments/objective-eval-lora-1.5b-v2-on-v3.yaml",
        ],
    ),
    (
        "qwen2.5-1.5b-lora-v3",
        "lora-1.5b-v3-test",
        [
            "evaluate",
            "--config",
            "../../configs/experiments/objective-eval-lora-1.5b-v3.yaml",
        ],
    ),
)


def now():
    return datetime.now(timezone.utc).isoformat()


def sha(path):
    with Path(path).open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def write_json(path, value):
    path = Path(path)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)


def check_files(root, hashes):
    for rel, expected in hashes.items():
        path = root / rel
        if not path.is_file() or sha(path) != expected:
            raise RuntimeError(f"Arquivo ausente ou hash divergente: {rel}")


def check_registration(root, commit, ci_run):
    if len(commit) != 40 or any(c not in "0123456789abcdef" for c in commit):
        raise RuntimeError("Commit de registro inválido")
    with urlopen(
        f"https://api.github.com/repos/{REPOSITORY}/actions/runs/{int(ci_run)}",
        timeout=60,
    ) as f:
        ci = json.load(f)
    if (ci["head_sha"], ci["status"], ci["conclusion"], ci["name"]) != (
        commit,
        "completed",
        "success",
        "CI",
    ):
        raise RuntimeError("A CI precisa estar verde no commit de registro")
    with urlopen(
        f"https://raw.githubusercontent.com/{REPOSITORY}/{commit}/{GATE_PATH}",
        timeout=60,
    ) as f:
        published = f.read()
    if published != (root / GATE_PATH).read_bytes():
        raise RuntimeError("Gate local difere do registro publicado")
    return {"commit": commit, "ciRun": ci_run, "verifiedAt": now()}


def run_once(root, journal, name, command, retry=False, execute=subprocess.run):
    """Reutiliza métricas produzidas; tentativa técnica exige registro e autorização explícita."""
    state_path = journal / f"{name}.json"
    output = journal / "artifacts" / name
    summary = output / "summary.json"
    predictions = output / "predictions.jsonl"
    state = json.loads(state_path.read_text()) if state_path.exists() else None
    if state and state["status"] == "completed":
        if state["summarySha256"] != sha(summary) or state["predictionsSha256"] != sha(
            predictions
        ):
            raise RuntimeError(f"Resultados alterados: {name}; proibido reavaliar")
        print("Reutilizando resultado concluído:", name, flush=True)
        return
    if summary.exists():
        # A desconexão pode ocorrer depois da gravação das métricas e antes do journal.
        # Nunca repetir a execução, mesmo se algum arquivo estiver incompleto/corrompido.
        if state is None or not predictions.exists():
            raise RuntimeError(
                f"Métricas preexistentes sem proveniência íntegra: {name}"
            )
        json.loads(summary.read_text())
        state.update(
            status="completed",
            recoveredWithoutRerun=True,
            finishedAt=now(),
            summarySha256=sha(summary),
            predictionsSha256=sha(predictions),
        )
        write_json(state_path, state)
        print("Métricas recuperadas sem reavaliar:", name, flush=True)
        return
    if state and (state["status"] != "technical_failure" or not retry):
        raise RuntimeError(
            f"{name}: tentativa anterior sem conclusão; trazer registro para revisão técnica"
        )
    if predictions.exists():
        raise RuntimeError(
            f"Predições preexistentes: {name}; revisar antes de qualquer nova tentativa"
        )
    attempt = (state["attempt"] if state else 0) + 1
    if state:
        write_json(journal / f"{name}-attempt-{state['attempt']}.json", state)
    state = {
        "status": "started",
        "attempt": attempt,
        "startedAt": now(),
        "command": command,
    }
    if attempt == 1:
        # Criação exclusiva impede que dois notebooks iniciem o mesmo sistema.
        with state_path.open("x") as f:
            json.dump(state, f)
    else:
        write_json(state_path, state)
    log = journal / f"{name}-attempt-{attempt}.log"
    print("Executando uma vez:", name, "— log:", log, flush=True)
    with log.open("w") as f:
        completed = execute(
            command, cwd=root / "ml/objectives", stdout=f, stderr=subprocess.STDOUT
        )
    if completed.returncode != 0:
        state.update(
            status="metrics_present" if summary.exists() else "technical_failure",
            returncode=completed.returncode,
            finishedAt=now(),
        )
        write_json(state_path, state)
        raise RuntimeError(
            f"Falha em {name}; resultados/log preservados. Não reiniciar o teste por conta própria."
        )
    if not summary.exists() or not predictions.exists():
        raise RuntimeError(
            f"Saída incompleta em {name}; revisar journal, sem repetir automaticamente"
        )
    state.update(
        status="completed",
        finishedAt=now(),
        summarySha256=sha(summary),
        predictionsSha256=sha(predictions),
    )
    write_json(state_path, state)


def collect_results(journal, gate):
    summaries = {
        version: json.loads((journal / "artifacts" / run / "summary.json").read_text())
        for version, run, _ in RUNS
    }
    reference = summaries[gate["gate"]["reference"]]["perClass"]["NEEDS_INPUT"][
        "recall"
    ]
    thresholds = gate["gate"]["thresholds"]
    results = []
    for version, run, _ in RUNS:
        summary = summaries[version]
        if (summary["n"], summary["corpusSha256"], summary["split"]) != (
            gate["corpus"]["testSize"],
            gate["corpus"]["testSha256"],
            "test",
        ):
            raise RuntimeError(
                f"Métricas não correspondem ao teste registrado: {version}"
            )
        predictions = [
            json.loads(l)
            for l in (journal / "artifacts" / run / "predictions.jsonl")
            .read_text()
            .splitlines()
        ]
        if (
            len(predictions) != summary["n"]
            or len({p["id"] for p in predictions}) != summary["n"]
        ):
            raise RuntimeError(f"Predições incompletas/duplicadas em {version}")
        recall = summary["perClass"]["NEEDS_INPUT"]["recall"]
        failures = []
        for failed, label in [
            (summary["jsonValidRate"] < thresholds["jsonValidRate"], "JSON"),
            (summary["macroF1"] < thresholds["macroF1"], "macroF1"),
            (
                recall < reference - thresholds["maxNeedsInputRecallDrop"],
                "needsInputRecall",
            ),
            (
                summary["latencyP95S"] > thresholds["maxLatencyP95Seconds"],
                "latência Colab",
            ),
        ]:
            if failed:
                failures.append(label)
        injection = [p for p in predictions if p["category"] == "injection"]
        errors = [p for p in predictions if p["pred"] != p["label"]]
        results.append(
            {
                "parserVersion": version,
                "provider": "RULES" if version.startswith("rules") else "LOCAL_MODEL",
                "jsonValidRate": summary["jsonValidRate"],
                "macroF1": summary["macroF1"],
                "needsInputRecall": recall,
                "latencyP95Seconds": summary["latencyP95S"],
                "approved": not failures,
                "failedCriteria": failures,
                "injection": {
                    "correct": sum(p["pred"] == p["label"] for p in injection),
                    "total": len(injection),
                },
                "errorsByCategory": {
                    c: sum(p["category"] == c for p in errors)
                    for c in sorted({p["category"] for p in errors})
                },
            }
        )
    rules_pass = next(
        r["approved"] for r in results if r["parserVersion"] == "rules_v2"
    )
    lora_pass = any(r["approved"] for r in results if r["provider"] == "LOCAL_MODEL")
    return {
        "results": results,
        "decision": {
            "defaultProvider": "RULES",
            "defaultParserVersion": "rules_v2" if rules_pass else "rules_v1",
            "requiresProviderDecision": not rules_pass and lora_pass,
            "note": "Aplicar ao backend somente após auditar o retorno; latência medida no Colab, não no desktop.",
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registration-commit", required=True)
    parser.add_argument("--ci-run", required=True, type=int)
    parser.add_argument("--journal", required=True, type=Path)
    parser.add_argument("--retry-technical", choices=[r[1] for r in RUNS])
    args = parser.parse_args()
    if not Path("/content/drive/MyDrive").is_dir():
        raise SystemExit(
            "Executar somente no Colab, com Drive montado para persistir o registro"
        )
    root = Path(__file__).resolve().parents[3]
    if not args.journal.resolve().is_relative_to(Path("/content/drive/MyDrive")):
        raise SystemExit(
            "Journal deve estar no Drive, nunca em armazenamento temporário"
        )
    import yaml

    gate = yaml.safe_load((root / GATE_PATH).read_text())
    if args.journal.name != sha(root / GATE_PATH):
        raise SystemExit("Journal não corresponde ao hash do gate registrado")
    registration = check_registration(root, args.registration_commit, args.ci_run)
    check_files(root, gate["freezeSha256"])
    import torch

    if not torch.cuda.is_available() or "T4" not in torch.cuda.get_device_name(0):
        raise SystemExit(
            "Gate registrado para GPU T4; não executar em outra GPU ou CPU"
        )
    from packaging.version import Version

    if Version(importlib.metadata.version("torch").split("+")[0]) < Version("2.6"):
        raise SystemExit("torch anterior a 2.6 não permitido")
    for package, expected in gate["environment"]["pinnedPackages"].items():
        if importlib.metadata.version(package) != expected:
            raise SystemExit(f"Versão divergente: {package}")
    args.journal.mkdir(parents=True, exist_ok=True)
    cpu_descriptor = {
        "model": next(
            line.split(":", 1)[1].strip()
            for line in Path("/proc/cpuinfo").read_text().splitlines()
            if line.startswith("model name")
        ),
        "logicalCpuCount": os.cpu_count(),
    }
    environment = {
        "registration": registration,
        "recordedBeforeRuns": now(),
        "python": platform.python_version(),
        "gpu": torch.cuda.get_device_name(0),
        "cuda": torch.version.cuda,
        "cpuDescriptor": cpu_descriptor,
        "packages": {
            p: importlib.metadata.version(p)
            for p in ["torch", *gate["environment"]["pinnedPackages"]]
        },
        "lscpu": subprocess.check_output(["lscpu"], text=True),
        "nvidiaSmi": subprocess.check_output(["nvidia-smi"], text=True),
    }
    env_path = args.journal / "environment.json"
    if env_path.exists():
        old = json.loads(env_path.read_text())
        for k in ["python", "gpu", "cuda", "packages", "cpuDescriptor"]:
            if old[k] != environment[k]:
                raise SystemExit(
                    "Ambiente mudou entre sistemas; preservar resultados e consultar o responsável"
                )
    else:
        write_json(env_path, environment)
    outputs = root / "artifacts/objectives"
    outputs.mkdir(parents=True, exist_ok=True)
    # Só os quatro diretórios de saída vão ao Drive; adaptadores ficam no disco do Colab.
    for _, run, _ in RUNS:
        durable = args.journal / "artifacts" / run
        durable.mkdir(parents=True, exist_ok=True)
        link = outputs / run
        if link.is_symlink():
            if link.resolve() != durable.resolve():
                raise SystemExit("Destino de resultados divergente")
        elif link.exists():
            raise SystemExit(
                "Saída local preexistente; preservar e revisar antes de avaliar"
            )
        else:
            link.symlink_to(durable, target_is_directory=True)
    check_files(root, gate["freezeSha256"])
    os.environ["HF_HUB_DISABLE_IMPLICIT_TOKEN"] = "1"
    os.environ["HF_HUB_DISABLE_XET"] = "1"
    from huggingface_hub import snapshot_download

    snapshot_download(
        repo_id=gate["environment"]["model"],
        revision=gate["environment"]["revision"],
        allow_patterns=[
            "*.json",
            "*.safetensors",
            "merges.txt",
            "vocab.json",
            "tokenizer.model",
        ],
        token=False,
    )
    os.environ["HF_HUB_OFFLINE"] = "1"
    write_json(args.journal / "gate.json", gate)
    (args.journal / "registered-gate.yaml").write_bytes((root / GATE_PATH).read_bytes())
    for version, run, tail in RUNS:
        check_files(root, gate["freezeSha256"])
        module, *options = tail
        command = [
            sys.executable,
            "-m",
            f"riftlab_objectives.{module}",
            *options,
            "--split",
            "test",
        ]
        if module == "evaluate":
            command += ["--run", run]
        run_once(root, args.journal, run, command, retry=args.retry_technical == run)
    report = collect_results(args.journal, gate)
    write_json(args.journal / "comparison.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    archive = Path("/content/riftlab-cc1404-resultados.zip")
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for p in sorted(args.journal.rglob("*")):
            if p.is_file() and p.suffix != ".safetensors":
                z.write(p, p.relative_to(args.journal))
    print("Retorno pronto:", archive)


if __name__ == "__main__":
    main()
