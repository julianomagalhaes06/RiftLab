"""Empacota exclusivamente entradas auditadas do gate v3 e gera notebook de avaliação."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import subprocess
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
GATE_PATH = "configs/objective-parser-gate-v3.yaml"
PROOF_PATH = "configs/objective-parser-gate-v3-registration.json"
REPOSITORY = "julianomagalhaes06/RiftLab"
PINS = (
    "transformers==5.19.0",
    "peft==0.21.2",
    "accelerate==1.15.0",
    "huggingface-hub==1.33.0",
    "pyyaml==6.0.3",
    "tokenizers==0.23.3",
    "safetensors==0.8.0",
)


def cell(kind, text):
    value = {"cell_type": kind, "metadata": {}, "source": text.strip().splitlines(True)}
    if kind == "code":
        value.update(execution_count=None, outputs=[])
    return value


def registration_proof(root, commit, ci_run, invoke=subprocess.check_output):
    """Confere GitHub no desktop; exporta só metadados, nunca credenciais ou dados de conta."""
    ci = json.loads(
        invoke(
            ["gh", "api", f"repos/{REPOSITORY}/actions/runs/{int(ci_run)}"], text=True
        )
    )
    if (ci["head_sha"], ci["status"], ci["conclusion"], ci["name"]) != (
        commit,
        "completed",
        "success",
        "CI",
    ):
        raise RuntimeError(
            "A CI precisa estar verde no commit de registro antes de empacotar"
        )
    remote = json.loads(
        invoke(
            [
                "gh",
                "api",
                f"repos/{REPOSITORY}/contents/{GATE_PATH}",
                "--method",
                "GET",
                "-f",
                f"ref={commit}",
            ],
            text=True,
        )
    )
    if remote["encoding"] != "base64":
        raise RuntimeError("Formato inesperado do gate publicado")
    published = base64.b64decode(remote["content"])
    if published != (root / GATE_PATH).read_bytes():
        raise RuntimeError("Gate local difere do registro publicado")
    return {
        "commit": commit,
        "ciRun": int(ci_run),
        "ciStatus": ci["status"],
        "ciConclusion": ci["conclusion"],
        "ciName": ci["name"],
        "gateSha256": hashlib.sha256(published).hexdigest(),
        "verifiedAt": datetime.now(timezone.utc).isoformat(),
        "verification": "CI e gate conferidos na API autenticada via gh durante a montagem no desktop; sem credenciais no pacote.",
    }


def build_notebook(commit, ci_run, bundle_sha, manifest, gate_sha):
    metadata = {"commit": commit, "ciRun": ci_run, "gateSha256": gate_sha}
    cells = [
        cell(
            "markdown",
            """
# RiftLab — avaliação única do gate v3 (CC-1404)

Avalia regras v1/v2 e LoRA v2/v3, sem treino. O gate foi publicado e sua CI passou antes
deste pacote. A montagem conferiu CI e gate na API autenticada do GitHub no desktop;
o notebook confere o comprovante e os hashes, sem receber credenciais do repositório.

1. No Colab, escolha **Ambiente de execução → Alterar tipo → GPU T4**.
2. Execute as células em ordem. Autorize o Drive para preservar o registro entre sessões.
3. Quando solicitado, envie **riftlab-cc1404-colab-input.zip**, sem descompactar.
4. Aguarde os quatro sistemas. No fim, baixe **riftlab-cc1404-resultados.zip** e devolva-o.

Cada sistema pode produzir métricas uma única vez. Reexecutar o notebook reaproveita os
resultados concluídos; tentativas interrompidas ficam bloqueadas para revisão técnica.
Não apague a pasta do Drive, não ajuste parâmetros nem troque a GPU para tentar melhorar
resultados. Se falhar, execute a última célula de exportação e envie o retorno com os logs.

A latência é deste Colab, não da CPU do desktop. As quatro ambiguidades separadas do teste
principal não fazem parte do pacote nem desta avaliação. São 296 frases no gate principal.
""",
        ),
        cell(
            "code",
            f"""
# 1. Dependências da avaliação; o torch CUDA do Colab é registrado, sem substituição.
import importlib.metadata, subprocess, sys
from packaging.version import Version
assert sys.version_info >= (3, 11), 'Este notebook requer Python 3.11 ou posterior'
assert Version(importlib.metadata.version('torch').split('+')[0]) >= Version('2.6'), 'torch incompatível'
subprocess.run([sys.executable, '-m', 'pip', 'install', '-q', *{list(PINS)!r}], check=True)
# O treino também remove torchao: não usamos quantização nem sua integração do peft.
subprocess.run([sys.executable, '-m', 'pip', 'uninstall', '-q', '-y', 'torchao'], check=True)
""",
        ),
        cell(
            "code",
            f"""
# 2. Registro persistente e identificação desta avaliação.
from google.colab import drive
drive.mount('/content/drive')
from pathlib import Path
import json, hashlib, zipfile
REGISTRATION = {metadata!r}
ROOT = Path('/content/riftlab')
JOURNAL = Path('/content/drive/MyDrive/RiftLab/cc1404') / REGISTRATION['gateSha256']
JOURNAL.mkdir(parents=True, exist_ok=True)
print('Registro persistente:', JOURNAL)
""",
        ),
        cell(
            "code",
            f"""
# 3. Upload do pacote auditado; não enviar outro ZIP do projeto ou credenciais.
from google.colab import files
EXPECTED_ZIP_SHA = {bundle_sha!r}
MANIFEST = {manifest!r}
bundle = Path('/content/riftlab-cc1404-colab-input.zip')
if not bundle.exists() or hashlib.sha256(bundle.read_bytes()).hexdigest() != EXPECTED_ZIP_SHA:
    uploaded = files.upload()
    assert set(uploaded) == {{'riftlab-cc1404-colab-input.zip'}}, 'Envie somente o pacote solicitado'
    bundle.write_bytes(uploaded['riftlab-cc1404-colab-input.zip'])
assert hashlib.sha256(bundle.read_bytes()).hexdigest() == EXPECTED_ZIP_SHA, 'ZIP não confere'
with zipfile.ZipFile(bundle) as archive:
    assert len(archive.namelist()) == len(MANIFEST) and set(archive.namelist()) == set(MANIFEST), 'Conteúdo inesperado'
    for name, expected in MANIFEST.items():
        relative = Path(name)
        assert not relative.is_absolute() and '..' not in relative.parts
        data = archive.read(name)
        assert hashlib.sha256(data).hexdigest() == expected, name
        dest = ROOT / relative
        if dest.exists():
            assert dest.read_bytes() == data, f'Arquivo local alterado: {{name}}'
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
print('Pacote conferido:', len(MANIFEST), 'arquivos')
""",
        ),
        cell(
            "code",
            """
# 4. Confere comprovante CI/registro, ambiente e hashes; baixa base e avalia uma vez por sistema.
import os
os.environ['HF_HOME'] = '/content/riftlab-hf-cache'
os.environ['HF_HUB_DISABLE_IMPLICIT_TOKEN'] = '1'
os.environ['HF_HUB_DISABLE_XET'] = '1'
command = [sys.executable, str(ROOT / 'ml/objectives/colab/evaluate_gate_v3.py'),
           '--registration-commit', REGISTRATION['commit'], '--ci-run', str(REGISTRATION['ciRun']),
           '--journal', str(JOURNAL)]
from datetime import datetime, timezone
log = JOURNAL / ('runner-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f') + '.log')
with log.open('w') as output:
    with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          text=True, bufsize=1) as process:
        for line in process.stdout:
            print(line, end='')
            output.write(line)
            output.flush()
        status = process.wait()
if status:
    raise RuntimeError('Execução interrompida. Execute somente a última célula e envie o ZIP com os logs.')
""",
        ),
        cell(
            "code",
            """
# 5. Exportar também em caso de falha: não repetir avaliações para obter o ZIP.
target = Path('/content/riftlab-cc1404-resultados.zip')
with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
    for path in sorted(JOURNAL.rglob('*')):
        if path.is_file() and path.suffix != '.safetensors':
            archive.write(path, path.relative_to(JOURNAL))
print('Retorno:', target, round(target.stat().st_size / 2**20, 2), 'MiB')
files.download(str(target))
""",
        ),
    ]
    return {
        "nbformat": 4,
        "nbformat_minor": 5,
        "metadata": {
            "accelerator": "GPU",
            "colab": {"gpuType": "T4", "provenance": []},
            "kernelspec": {"name": "python3", "display_name": "Python 3"},
            "language_info": {"name": "python"},
        },
        "cells": cells,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registration-commit", required=True)
    parser.add_argument("--ci-run", required=True, type=int)
    parser.add_argument(
        "--output", type=Path, default=ROOT / ".private/colab/sprint14-cc1404"
    )
    args = parser.parse_args()
    import yaml

    gate = yaml.safe_load((ROOT / GATE_PATH).read_text())
    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()
    if head != args.registration_commit:
        raise SystemExit(
            "Gerar o pacote no commit de registro, sem alterações posteriores"
        )
    files = [GATE_PATH, *gate["freezeSha256"]]
    manifest = {}
    for name in files:
        data = (ROOT / name).read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if name != GATE_PATH and digest != gate["freezeSha256"][name]:
            raise SystemExit(f"Hash divergente antes de empacotar: {name}")
        manifest[name] = digest
    proof = registration_proof(ROOT, args.registration_commit, args.ci_run)
    proof_bytes = (json.dumps(proof, ensure_ascii=False, indent=2) + "\n").encode()
    manifest[PROOF_PATH] = hashlib.sha256(proof_bytes).hexdigest()
    args.output.mkdir(parents=True, exist_ok=True)
    bundle = args.output / "riftlab-cc1404-colab-input.zip"
    with zipfile.ZipFile(bundle, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for name in files:
            z.write(ROOT / name, name)
        z.writestr(PROOF_PATH, proof_bytes)
    notebook = build_notebook(
        args.registration_commit,
        args.ci_run,
        hashlib.sha256(bundle.read_bytes()).hexdigest(),
        manifest,
        manifest[GATE_PATH],
    )
    target = args.output / "riftlab-cc1404-avaliacao-colab.ipynb"
    target.write_text(json.dumps(notebook, ensure_ascii=False, indent=1) + "\n")
    (args.output / "SHA256SUMS").write_text(
        "".join(
            f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n"
            for p in [bundle, target]
        )
    )
    print(target)
    print(bundle, round(bundle.stat().st_size / 2**20, 2), "MiB")


if __name__ == "__main__":
    main()
