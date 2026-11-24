"""Monta o pacote auditado e o notebook do treino de late game no Colab (CC-1503).

Treino conjunto 16.23 (elo alto, gate) + 16.19, pré-registro 04.
Saída (privada, tem dados de partidas): `.private/colab/sprint15-cc1503-1623/`
- `riftlab-cc1503-1623-colab-input.zip`: código do backend, perfis 16.19 e 16.23, linhas de
  late game dos dois patches, item.json do Data Dragon e o pré-registro selado;
- `riftlab-cc1503-1623-treino-colab.ipynb`: confere o SHA-256 do ZIP e de cada arquivo, roda
  `run_cc1503.py` e exporta `riftlab-cc1503-1623-resultados.zip`;
- `SHA256SUMS` e `COMO-USAR.md`.

Uso (na raiz do repositório): python ml/late/colab/build_cc1503_notebook.py
"""

from __future__ import annotations

import hashlib
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / ".private" / "colab" / "sprint15-cc1503-1623"
ZIP_NAME = "riftlab-cc1503-1623-colab-input.zip"
NOTEBOOK_NAME = "riftlab-cc1503-1623-treino-colab.ipynb"
RESULT_NAME = "riftlab-cc1503-1623-resultados.zip"
PRIVATE_FILES = (
    ".private/dataset/late-16.23-v3.jsonl.gz",
    ".private/dataset/late-16.23-v3.jsonl.gz.sha256",
    ".private/dataset/late-16.19-v2.jsonl.gz",
    ".private/dataset/late-16.19-v2.jsonl.gz.sha256",
    ".private/research/data-dragon/item-16.23.1-en_US.json",
    ".private/research/data-dragon/item-16.23.1-pt_BR.json",
    ".private/research/data-dragon/item-16.19.1-en_US.json",
    ".private/validation/sprint-15/CC-1503/04-pre-registro-16.23.md",
    ".private/validation/sprint-15/CC-1503/04-pre-registro-16.23.md.sha256",
)
PUBLIC_FILES = (
    "configs/profiles/profiles-16.19.json",
    "configs/profiles/profiles-16.23.json",
    "ml/late/colab/run_cc1503.py",
    "backend/pyproject.toml",
)
PACKAGES = {"catboost": "1.2.10", "typer": "0.27.2", "pyyaml": "6.0.3"}


GUIDE = """# Treino do late game no Colab (CC-1503, 16.23 + 16.19)

1. Abra https://colab.research.google.com → Arquivo → Fazer upload de notebook →
   `{nb}`.
2. Ambiente de execução → Alterar tipo de ambiente → **GPU T4** → Salvar.
3. Execute as células 1 a 5 em ordem. Na 2, autorize o Google Drive. Na 3, envie
   `{zip}` (não descompacte).
4. A célula 4 faz: fumaça (~2 min) → treino completo (GPU, dezenas de minutos) → teste único →
   gate → folha de revisão cega. Se a sessão cair, rode tudo de novo com o mesmo ZIP: o que já
   terminou é pulado. Se aparecer "o teste começou numa sessão anterior", não insista: vá para a
   célula 5 e devolva o ZIP.
5. A célula 5 baixa `{result}`. Coloque em
   `.private/colab/sprint15-cc1503-1623/retorno/` e avise o Claude.

Conferência: `sha256sum -c SHA256SUMS` nesta pasta (ZIP {zip_sha}…, notebook {nb_sha}…).
"""


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def collect() -> list[str]:
    files = [*PRIVATE_FILES, *PUBLIC_FILES]
    for path in sorted((ROOT / "backend" / "src" / "riftlab").rglob("*.py")):
        files.append(str(path.relative_to(ROOT)))
    missing = [f for f in files if not (ROOT / f).is_file()]
    if missing:
        raise SystemExit(f"arquivos ausentes: {missing}")
    # Pré-registro e linhas têm de bater com os selos antes de sair da máquina.
    for document in PRIVATE_FILES:
        if document.endswith((".md", ".jsonl.gz")):
            sealed = (ROOT / (document + ".sha256")).read_text().split()[0]
            if sha256(ROOT / document) != sealed:
                raise SystemExit(f"{document} difere do selo")
    return files


def build_zip(files: list[str]) -> dict[str, str]:
    OUT.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = OUT / ZIP_NAME
    if target.exists():
        raise SystemExit(f"{target} já existe: não sobrescrever um pacote auditado")
    manifest = {f: sha256(ROOT / f) for f in files}
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in files:
            info = zipfile.ZipInfo(name, date_time=(2026, 11, 24, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, (ROOT / name).read_bytes())
    return manifest


def md(text: str) -> dict:
    return {
        "cell_type": "markdown",
        "metadata": {},
        "source": text.strip().splitlines(True),
    }


def code(text: str) -> dict:
    return {
        "cell_type": "code",
        "execution_count": None,
        "metadata": {},
        "outputs": [],
        "source": text.strip().splitlines(True),
    }


def notebook(zip_sha: str, manifest: dict[str, str]) -> dict:
    cells = [
        md(f"""
# RiftLab — treino do modelo de late game (CC-1503, 16.23 + 16.19)

Treina o modelo contextual do 3º ao 6º item principal (qualquer campeão) com as partidas de
elo alto do 16.23 e o corpus 16.19, abre o teste **uma única vez** com o pré-registro 04 selado
e decide o gate no teste do 16.23 (o do 16.19 é secundário).

1. **Ambiente de execução → Alterar tipo → GPU T4.**
2. Execute as células em ordem. Autorize o Drive: o registro guarda cada etapa concluída e
   impede reabrir o teste em outra sessão.
3. Quando pedido, envie **{ZIP_NAME}** sem descompactar.
4. Etapas: fumaça (recorte só de treino e validação), treino completo (dezenas de minutos na
   GPU), teste único, gate e folha de revisão cega.
5. No fim, baixe **{RESULT_NAME}** e devolva-o (o modelo `model.cbm` vai dentro).

Se a sessão cair, execute de novo do início com o mesmo ZIP: as etapas concluídas são
puladas. Não troque parâmetros nem apague a pasta do Drive. Se o teste tiver começado e não
houver relatório, o notebook para e pede revisão (não reabre).
"""),
        code(f"""
# 1. Dependências (versões travadas no projeto) e GPU.
import subprocess, sys, json
assert sys.version_info >= (3, 11), 'Python 3.11+ necessário'
subprocess.run([sys.executable, '-m', 'pip', 'install', '-q',
                *[f'{{k}}=={{v}}' for k, v in {PACKAGES!r}.items()]], check=True)
import catboost, numpy
from catboost.utils import get_gpu_device_count
GPUS = get_gpu_device_count()
print('Python', sys.version.split()[0], '| catboost', catboost.__version__,
      '| numpy', numpy.__version__, '| GPUs', GPUS)
if GPUS == 0:
    print('ATENÇÃO: sem GPU. Troque para GPU T4 antes de seguir '
          '(Ambiente de execução → Alterar tipo).')
"""),
        code(f"""
# 2. Registro persistente no Drive, por pacote.
from google.colab import drive
drive.mount('/content/drive')
from pathlib import Path
EXPECTED_ZIP_SHA = '{zip_sha}'
JOURNAL = Path('/content/drive/MyDrive/RiftLab/cc1503-1623') / EXPECTED_ZIP_SHA[:16]
JOURNAL.mkdir(parents=True, exist_ok=True)
print('Registro:', JOURNAL)
print('Etapas já concluídas:', sorted(p.name for p in JOURNAL.iterdir()))
"""),
        code(f"""
# 3. Upload e conferência do pacote auditado (SHA-256 do ZIP e de cada arquivo).
import hashlib, zipfile, shutil
from google.colab import files
MANIFEST = {json.dumps(manifest, sort_keys=True)}
uploaded = files.upload()
name = '{ZIP_NAME}'
assert name in uploaded, f'envie {{name}}'
data = uploaded[name]
assert hashlib.sha256(data).hexdigest() == EXPECTED_ZIP_SHA, 'ZIP diferente do auditado'
ROOT = Path('/content/riftlab')
shutil.rmtree(ROOT, ignore_errors=True)
Path('/content', name).write_bytes(data)
with zipfile.ZipFile(Path('/content', name)) as archive:
    assert sorted(archive.namelist()) == sorted(MANIFEST), 'lista de arquivos diferente'
    archive.extractall(ROOT)
for relative, digest in MANIFEST.items():
    assert hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() == digest, relative
print('Pacote conferido:', len(MANIFEST), 'arquivos')
"""),
        code("""
# 4. Fumaça → treino → teste único → gate → revisão cega (etapas concluídas são puladas).
import os
from datetime import datetime, timezone
task = 'GPU' if GPUS else 'CPU'
log = JOURNAL / ('execucao-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '.log')
command = [sys.executable, str(ROOT / 'ml/late/colab/run_cc1503.py'), '--root', str(ROOT),
           '--journal', str(JOURNAL), '--task-type', task]
with log.open('w') as output, subprocess.Popen(command, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, bufsize=1) as process:
    for line in process.stdout:
        print(line, end='')
        output.write(line)
        output.flush()
print('código de saída:', process.returncode)
"""),
        code(f"""
# 5. Exportar o registro (também em caso de falha: não repetir etapas para obter o ZIP).
target = Path('/content/{RESULT_NAME}')
with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
    for path in sorted(JOURNAL.rglob('*')):
        if path.is_file():
            archive.write(path, path.relative_to(JOURNAL))
print('Retorno:', target, round(target.stat().st_size / 2**20, 2), 'MiB')
files.download(str(target))
"""),
    ]
    return {
        "cells": cells,
        "metadata": {
            "accelerator": "GPU",
            "colab": {"gpuType": "T4", "provenance": []},
            "kernelspec": {"display_name": "Python 3", "name": "python3"},
            "language_info": {"name": "python"},
        },
        "nbformat": 4,
        "nbformat_minor": 0,
    }


def main() -> None:
    files = collect()
    manifest = build_zip(files)
    zip_sha = sha256(OUT / ZIP_NAME)
    (OUT / NOTEBOOK_NAME).write_text(
        json.dumps(notebook(zip_sha, manifest), ensure_ascii=False, indent=1) + "\n"
    )
    sums = "".join(f"{sha256(OUT / n)}  {n}\n" for n in (ZIP_NAME, NOTEBOOK_NAME))
    (OUT / "SHA256SUMS").write_text(sums)
    size = (OUT / ZIP_NAME).stat().st_size / 2**20
    nb_sha = sha256(OUT / NOTEBOOK_NAME)
    (OUT / "COMO-USAR.md").write_text(
        GUIDE.format(
            zip=ZIP_NAME,
            nb=NOTEBOOK_NAME,
            result=RESULT_NAME,
            zip_sha=zip_sha[:8],
            nb_sha=nb_sha[:8],
        )
    )
    summary = {
        "zip": ZIP_NAME,
        "sha256": zip_sha,
        "files": len(files),
        "MiB": round(size, 1),
    }
    print(json.dumps(summary))
    sys.stdout.flush()


if __name__ == "__main__":
    main()
