#!/usr/bin/env python3
"""CC-703: monta o pacote do Colab, sem identificadores de partida.

- Datasets v4 (compra, planejamento, contexto rico): `matchId` vira um índice opaco (m000001…),
  o mesmo nos três arquivos. O mapa fica só aqui, em `bundle-map.json` (privado, nunca no pacote).
  Mais nada muda: `playerSeenInTrain`, partes, fatias e features seguem iguais.
- Código: backend/src/riftlab (o mesmo do repositório, com as mudanças do CC-703).
- Catálogos públicos do Data Dragon 16.19.1 (itens e campeões).
- Scripts: bench_train.py e train_colab.py. MANIFEST.json traz o SHA-256 de cada arquivo e o do
  dataset original correspondente.

Rodar (em backend/): uv run python ../.private/colab/CC-703/make_bundle.py
"""

from __future__ import annotations

import hashlib
import json
import os
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
PRIVATE = HERE.parents[1]
REPO = PRIVATE.parent
DATASETS = {
    "dataset-v4-features.json": "dataset-16.19-v4-features-slices.json",
    "dataset-v4-planning.json": "dataset-16.19-v4-planning-slices.json",
    "dataset-v4-planning-ctx.json": "dataset-16.19-v4-planning-ctx-slices.json",
}
CATALOGS = {
    "champion-16.19.1-en_US.json": PRIVATE / "validation/sprint-4/CC-402/catalog/champion-16.19.1-en_US.json",
    "item-16.19.1-en_US.json": PRIVATE / "validation/sprint-3/CC-321/catalog/item-16.19.1-en_US.json",
}


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def verified(path: Path) -> bytes:
    raw = path.read_bytes()
    if path.with_suffix(path.suffix + ".sha256").read_text() != f"{sha(raw)}  {path.name}\n":
        raise SystemExit(f"ABORTADO: sidecar diverge em {path.name}")
    return raw


def main() -> None:
    out_zip = HERE / "riftlab-cc703-colab.zip"
    if out_zip.exists():
        raise SystemExit("ABORTADO: pacote já existe; apague só depois de conferir")
    originals = {name: verified(PRIVATE / "dataset" / source) for name, source in DATASETS.items()}
    documents = {name: json.loads(raw) for name, raw in originals.items()}
    ids = sorted({row["matchId"] for doc in documents.values() for row in doc["rows"]})
    alias = {match_id: f"m{index:06d}" for index, match_id in enumerate(ids, 1)}
    manifest: dict[str, object] = {"card": "CC-703", "files": {}, "originals": {}}
    files: dict[str, bytes] = {}
    for name, doc in documents.items():
        rows = [{**row, "matchId": alias[row["matchId"]]} for row in doc["rows"]]
        pseudo = {**{k: v for k, v in doc.items() if k != "rows"}, "rows": rows, "matchIdsPseudonymized": True}
        raw = (json.dumps(pseudo, sort_keys=True) + "\n").encode()
        if any(match_id.encode() in raw for match_id in ids[:50]):
            raise SystemExit("ABORTADO: id de partida sobrou no pacote")
        files[f"data/{name}"] = raw
        manifest["originals"][name] = {"source": DATASETS[name], "sha256": sha(originals[name])}
    for name, path in CATALOGS.items():
        files[f"catalog/{name}"] = path.read_bytes()
    for script in ("bench_train.py", "train_colab.py"):
        files[script] = (HERE / script).read_bytes()
    src = REPO / "backend" / "src"
    for path in sorted((src / "riftlab").rglob("*.py")):
        files[f"src/{path.relative_to(src)}"] = path.read_bytes()
    manifest["files"] = {name: sha(raw) for name, raw in sorted(files.items())}
    files["MANIFEST.json"] = (json.dumps(manifest, indent=1, sort_keys=True) + "\n").encode()
    with zipfile.ZipFile(out_zip, "x", compression=zipfile.ZIP_DEFLATED) as bundle:
        for name, raw in sorted(files.items()):
            bundle.writestr(name, raw)
    map_path = HERE / "bundle-map.json"
    fd = os.open(map_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400)
    os.write(fd, (json.dumps({"alias": alias}, sort_keys=True) + "\n").encode())
    os.close(fd)
    print(json.dumps({"zip": out_zip.name, "sha256": sha(out_zip.read_bytes()), "matches": len(ids),
                      "sizeMb": round(out_zip.stat().st_size / 1e6, 1)}))


if __name__ == "__main__":
    main()
