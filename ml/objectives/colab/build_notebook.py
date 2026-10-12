"""CC-503: gera o notebook do Colab que treina o LoRA em GPU com o mesmo código e dados.

Uso: python3 build_notebook.py  → riftlab-objective-lora-colab.ipynb (ao lado deste arquivo)

O notebook é autocontido: embute prompt.py, train.py, evaluate.py, metrics.py e os splits de
treino e validação (gzip+base64), confere o SHA-256 de cada arquivo ao recriá-lo e nunca inclui
o conjunto de teste. Ao final empacota adaptadores, manifesto, log, conferência na validação e o
ambiente (GPU, versões) num zip para trazer de volta ao projeto. Nenhuma chave ou dado pessoal.
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
OBJ = HERE.parent
EMBED = [
    "riftlab_objectives/__init__.py",
    "riftlab_objectives/prompt.py",
    "riftlab_objectives/metrics.py",
    "riftlab_objectives/train.py",
    "riftlab_objectives/evaluate.py",
    "corpus/train-v1.jsonl",
    "corpus/val-v1.jsonl",
]
PINS = "transformers==4.46.3 peft==0.13.2 accelerate==1.15.0 huggingface-hub==0.36.2 pyyaml"
MODELS = {
    "1.5b": ("Qwen/Qwen2.5-1.5B-Instruct", "989aa7980e4cf806f80c7fef2b1adb7bc71aa306"),
    "0.5b": ("Qwen/Qwen2.5-0.5B-Instruct", "7ae557604adf67be50417f59c2c2f167def9a775"),
}


def md(text: str) -> dict:
    return {"cell_type": "markdown", "metadata": {}, "source": text.strip("\n").splitlines(True)}


def code(text: str) -> dict:
    return {
        "cell_type": "code",
        "metadata": {},
        "execution_count": None,
        "outputs": [],
        "source": text.strip("\n").splitlines(True),
    }


def payload() -> dict[str, tuple[str, str]]:
    files = {}
    for rel in EMBED:
        data = (OBJ / rel).read_bytes()
        files[f"ml/objectives/{rel}"] = (
            hashlib.sha256(data).hexdigest(),
            base64.b64encode(gzip.compress(data, mtime=0)).decode(),
        )
    return files


def build() -> dict:
    files = payload()
    cells = [
        md(f"""
# RiftLab — ajuste LoRA do interpretador de objetivo (CC-503)

Treina um adaptador LoRA sobre o **Qwen2.5-Instruct** (revisão fixada, licença Apache-2.0) para
converter uma frase em português numa das prioridades `BALANCED`, `SURVIVAL`, `DAMAGE`,
`UTILITY` ou `NEEDS_INPUT`. É o mesmo código e os mesmos dados do treino local em CPU; aqui só
roda em GPU.

**Como rodar**

1. Menu **Ambiente de execução → Alterar o tipo de ambiente de execução → GPU T4** → Salvar.
2. Menu **Ambiente de execução → Executar tudo**.
3. Leva uns 20–30 min com o modelo 1.5B. No fim, um arquivo `riftlab-lora-*.zip` é baixado.
4. Coloque o zip na pasta `artifacts/objectives/` do projeto e me avise.

**O que vai e o que não vai para o Colab:** código de treino e corpus sintético de treino e
validação (sem dados de partida nem de jogador). O **conjunto de teste não está aqui**: a
avaliação oficial e a medição de latência são feitas depois, na CPU local. Nenhuma chave é usada;
os pesos vêm do Hugging Face público.

Arquivos embutidos (SHA-256 conferido ao recriar):
""" + "\n".join(f"- `{k}` `{v[0][:16]}…`" for k, v in files.items())),
        code("""
# 1) GPU disponível?
import subprocess, torch
print(subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv"],
                     capture_output=True, text=True).stdout or "nvidia-smi indisponível")
assert torch.cuda.is_available(), (
    "Sem GPU. Menu Ambiente de execução → Alterar o tipo de ambiente de execução → GPU T4, "
    "depois Executar tudo de novo.")
print("torch", torch.__version__, "CUDA", torch.version.cuda, "GPU", torch.cuda.get_device_name(0))
"""),
        code(f"""
# 2) Bibliotecas fixadas (as mesmas versões do ambiente local, exceto o torch do Colab)
!pip install -q {PINS}
import importlib.metadata as md
for name in ["torch", "transformers", "peft", "accelerate", "tokenizers", "huggingface-hub"]:
    print(name, md.version(name))
"""),
        code(
            "# 3) Recria código e corpus com conferência de hash (não edite)\n"
            "import base64, gzip, hashlib, os\n"
            "ROOT = '/content/riftlab'\n"
            f"FILES = {json.dumps(files, indent=0)}\n"
            "for rel, (sha, blob) in FILES.items():\n"
            "    data = gzip.decompress(base64.b64decode(blob))\n"
            "    assert hashlib.sha256(data).hexdigest() == sha, f'hash divergente em {rel}'\n"
            "    path = os.path.join(ROOT, rel)\n"
            "    os.makedirs(os.path.dirname(path), exist_ok=True)\n"
            "    open(path, 'wb').write(data)\n"
            "    print('ok', rel, sha[:16])\n"
        ),
        code(
            "# 4) Configuração (mesmos hiperparâmetros do 0.5B local; troque para '0.5b' só para\n"
            "#    conferir reprodutibilidade)\n"
            "MODEL_SIZE = '1.5b'  # @param ['1.5b', '0.5b']\n"
            f"MODELS = {json.dumps(MODELS)}\n"
            "import yaml\n"
            "model, revision = MODELS[MODEL_SIZE]\n"
            "RUN = f'lora-{MODEL_SIZE}-colab'\n"
            "config = {\n"
            "    'model': model, 'revision': revision, 'license': 'apache-2.0', 'seed': 503,\n"
            "    'threads': 4, 'device': 'auto', 'epochs': 3, 'batchSize': 4, 'gradAccumulation': 4,\n"
            "    'gradientCheckpointing': True, 'learningRate': 0.0002, 'maxHours': 3,\n"
            "    'maxNewTokens': 12,\n"
            "    'lora': {'r': 16, 'alpha': 32, 'dropout': 0.05,\n"
            "             'targetModules': ['q_proj', 'k_proj', 'v_proj', 'o_proj']},\n"
            "    'output': f'artifacts/objectives/{RUN}',\n"
            "}\n"
            "os.makedirs(f'{ROOT}/configs/experiments', exist_ok=True)\n"
            "CONFIG = f'{ROOT}/configs/experiments/objective-{RUN}.yaml'\n"
            "open(CONFIG, 'w').write(yaml.safe_dump(config, sort_keys=False))\n"
            "print(open(CONFIG).read())\n"
        ),
        code("""
# 5) Treino (perda por lote e perda na validação ao fim de cada época)
!cd /content/riftlab/ml/objectives && python -m riftlab_objectives.train --config "$CONFIG"
"""),
        code(
            "# 6) Conferência rápida na validação com o adaptador de menor perda (os números\n"
            "#    oficiais serão medidos na CPU local, junto com o teste reservado)\n"
            "import json\n"
            "manifest = json.load(open(f'{ROOT}/{config[\"output\"]}/manifest.json'))\n"
            "print({k: manifest[k] for k in ['stopReason', 'bestEpoch', 'valLossByEpoch', 'hours',\n"
            "                                'device', 'gpu', 'cudaMaxMemoryMb']})\n"
            "check = {'model': model, 'revision': revision, 'adapter': manifest['adapter'],\n"
            "         'seed': 0, 'device': 'auto', 'maxNewTokens': 12}\n"
            "CHECK = f'{ROOT}/configs/experiments/objective-eval-{RUN}.yaml'\n"
            "open(CHECK, 'w').write(yaml.safe_dump(check, sort_keys=False))\n"
        ),
        code("""
!cd /content/riftlab/ml/objectives && python -m riftlab_objectives.evaluate --config "$CHECK" --split val --run "$RUN-val-gpu"
"""),
        code(
            "# 7) Empacota e baixa: adaptadores, manifesto, log, conferência e ambiente\n"
            "import importlib.metadata as md, platform, shutil, subprocess\n"
            "env = {\n"
            "    'gpu': torch.cuda.get_device_name(0), 'cuda': torch.version.cuda,\n"
            "    'python': platform.python_version(),\n"
            "    'packages': {n: md.version(n) for n in ['torch', 'transformers', 'peft',\n"
            "                 'accelerate', 'tokenizers', 'huggingface-hub']},\n"
            "    'embeddedSha256': {k: v[0] for k, v in FILES.items()},\n"
            "    'nvidiaSmi': subprocess.run(['nvidia-smi'], capture_output=True, text=True).stdout,\n"
            "}\n"
            "out = f'{ROOT}/artifacts/objectives'\n"
            "json.dump(env, open(f'{out}/{RUN}/colab-environment.json', 'w'), indent=2)\n"
            "shutil.copytree(f'{out}/{RUN}-val-gpu', f'{out}/{RUN}/val-gpu', dirs_exist_ok=True)\n"
            "archive = shutil.make_archive(f'/content/riftlab-{RUN}', 'zip', out, RUN)\n"
            "print(archive, round(os.path.getsize(archive) / 2**20, 1), 'MB')\n"
            "from google.colab import files\n"
            "files.download(archive)\n"
        ),
    ]
    return {
        "nbformat": 4,
        "nbformat_minor": 5,
        "metadata": {
            "accelerator": "GPU",
            "colab": {"provenance": [], "gpuType": "T4"},
            "kernelspec": {"name": "python3", "display_name": "Python 3"},
            "language_info": {"name": "python"},
        },
        "cells": cells,
    }


def main() -> None:
    target = HERE / "riftlab-objective-lora-colab.ipynb"
    target.write_text(json.dumps(build(), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(target, round(target.stat().st_size / 1024), "KB")


if __name__ == "__main__":
    main()
