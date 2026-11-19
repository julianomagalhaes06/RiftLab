"""Comandos do late game (Sprint 15): perfis, linhas, treino, avaliação e conhecimento."""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import sys
import time
from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import typer

late_app = typer.Typer(
    help="Late game (3º–6º item principal, qualquer campeão): dados, modelo e conhecimento.",
    no_args_is_help=True,
)

_ROOT = Path(__file__).resolve().parents[4]
_PRIVATE = _ROOT / ".private"
_CONFIGS = _ROOT / "configs"
_DD_HOST = "https://ddragon.leagueoflegends.com/cdn"


def _require_new_private(path: Path) -> None:
    if ".private" not in path.resolve().parts or path.exists():
        typer.echo("BLOCKED: a saída deve ser nova e ficar em .private", err=True)
        raise typer.Exit(code=1)


def write_jsonl_gz(path: Path, rows: Iterator[dict[str, Any]] | list[dict[str, Any]]) -> str:
    """Grava uma vez (O_EXCL, 0600) e devolve o SHA-256 do arquivo comprimido."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")).encode() + b"\n")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    path.with_name(path.name + ".sha256").write_text(f"{digest}  {path.name}\n")
    return digest


def read_jsonl_gz(path: Path) -> Iterator[dict[str, Any]]:
    with gzip.open(path, "rb") as handle:
        for line in handle:
            yield json.loads(line)


def _fetch_data_dragon(version: str, locale: str, name: str, cache: Path) -> bytes:
    """Arquivo estático público do Data Dragon, guardado em cache privado."""
    target = cache / f"{name}-{version}-{locale}.json"
    if target.exists():
        return target.read_bytes()
    import httpx

    response = httpx.get(f"{_DD_HOST}/{version}/data/{locale}/{name}.json", timeout=60)
    response.raise_for_status()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(response.content)
    return response.content


@late_app.command("profiles")
def late_profiles(
    patch: str = typer.Option(..., help="Patch major.minor (ex.: 16.19)."),
    data_dragon_version: str = typer.Option(..., help="Versão do Data Dragon (ex.: 16.19.1)."),
    cache: Path = typer.Option(_PRIVATE / "research" / "data-dragon", help="Cache privado."),
) -> None:
    """Gera configs/profiles/profiles-<patch>.json (atributos e mecânicas de itens, traços de
    campeões) a partir do Data Dragon em inglês."""
    from riftlab.profiles.domain import build_profiles, profiles_path

    item = _fetch_data_dragon(data_dragon_version, "en_US", "item", cache)
    champions = _fetch_data_dragon(data_dragon_version, "en_US", "championFull", cache)
    profiles = build_profiles(patch, data_dragon_version, item, champions)
    target = profiles_path(_CONFIGS, patch)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(profiles.to_document(), ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    typer.echo(
        json.dumps(
            {
                "items": len(profiles.items),
                "champions": len(profiles.champions),
                "path": str(target),
            }
        )
    )


# Estado dos processos filhos (fork): carregado uma vez por processo.
_WORKER: dict[str, Any] = {}


def _init_worker(config: dict[str, Any]) -> None:
    import asyncio

    from riftlab.dataset.late_corpus import RawIndex, catalog_from_data_dragon, ephemeral_secret
    from riftlab.profiles.domain import Profiles
    from riftlab.timeline.application import RECONSTRUCTION_ALGORITHM_VERSION
    from riftlab.timeline.empirical_rules import load_empirical_rules

    item_bytes = Path(config["itemJson"]).read_bytes()
    _WORKER.update(
        bundle=catalog_from_data_dragon(
            json.loads(item_bytes),
            config["patch"],
            config["dataDragonVersion"],
            hashlib.sha256(item_bytes).hexdigest(),
        ),
        empirical=load_empirical_rules(
            Path(config["empiricalRules"]), reconstruction_version=RECONSTRUCTION_ALGORITHM_VERSION
        ),
        item_rules=_resolve_rules(config, item_bytes),
        profiles=Profiles.from_document(json.loads(Path(config["profiles"]).read_text())),
        index=RawIndex([Path(p) for p in config["rawRoots"]]),
        secret=ephemeral_secret(),
        runner=asyncio.Runner(),
    )


def _resolve_rules(config: dict[str, Any], item_bytes: bytes) -> Any:
    """Manifesto próprio do patch; senão o herdado do último patch revisado, com os itens
    principais novos ou alterados (receita/preço/classe) marcados como não cobertos, como na
    análise do produto (`candidates.rules.resolve_catalog_item_rules`)."""
    from riftlab.candidates.rules import load_item_rules, resolve_item_rules
    from riftlab.catalog.diff import catalog_diff
    from riftlab.dataset.late_corpus import catalog_from_data_dragon

    path = Path(config["itemRules"])
    if path.is_file():
        return load_item_rules(path)
    patch = config["patch"]
    target = catalog_from_data_dragon(
        json.loads(item_bytes),
        patch,
        config["dataDragonVersion"],
        hashlib.sha256(item_bytes).hexdigest(),
    )

    def mains(bundle: Any) -> set[int]:
        return {
            i for i in bundle.by_id if bundle.classification.classify(i).item_class.value == "MAIN"
        }

    first = resolve_item_rules(path, patch, mains(target))
    if first is None or first.inherited_from is None:
        return first
    source_bytes = Path(config["sourceItemJson"]).read_bytes()
    source = catalog_from_data_dragon(
        json.loads(source_bytes),
        first.inherited_from,
        "source",
        hashlib.sha256(source_bytes).hexdigest(),
    )
    changed = set(
        catalog_diff(source.by_id, target.by_id, source.classification, target.classification)
    ) & (mains(target) | mains(source))
    return resolve_item_rules(path, patch, mains(target), changed_item_ids=changed)


def _extract(pair: tuple[str, str]) -> tuple[list[dict[str, Any]], Counter[str]]:
    from riftlab.dataset.late_corpus import extract_match

    split, match_id = pair
    bodies = _WORKER["index"].read(match_id)
    if bodies is None:
        return [], Counter({"RAW_MISSING": 1})
    try:
        return extract_match(
            match_id,
            split,
            bodies[0],
            bodies[1],
            bundle=_WORKER["bundle"],
            empirical=_WORKER["empirical"],
            item_rules=_WORKER["item_rules"],
            profiles=_WORKER["profiles"],
            secret=_WORKER["secret"],
            runner=_WORKER["runner"],
        )
    except Exception as exc:  # noqa: BLE001 — uma partida ruim não derruba o corpus
        return [], Counter({f"MATCH_ERROR_{type(exc).__name__}": 1})


@late_app.command("build-rows")
def late_build_rows(
    manifest: Path = typer.Option(..., help="Manifesto congelado do corpus (divisão por partida)."),
    item_json: Path = typer.Option(..., help="item.json do Data Dragon (en_US) do patch."),
    patch: str = typer.Option(..., help="Patch major.minor do corpus."),
    data_dragon_version: str = typer.Option(..., help="Versão do Data Dragon do item.json."),
    output: Path = typer.Option(..., help="Saída privada nova (.jsonl.gz)."),
    item_rules: Path = typer.Option(None, help="Regras de itens (padrão: configs/item-rules)."),
    raw_root: list[Path] = typer.Option(
        [_PRIVATE / "collection"], help="Raízes privadas com os pares brutos (repetível)."
    ),
    source_item_json: Path = typer.Option(
        None, help="item.json do patch de origem das regras herdadas (patch sem manifesto)."
    ),
    workers: int = typer.Option(4, min=1, max=12),
    limit: int = typer.Option(0, min=0, help="Só as N primeiras partidas (teste rápido)."),
) -> None:
    """Linhas de late game (3º–6º, qualquer campeão) do corpus bruto, sem banco nem rede."""
    from concurrent.futures import ProcessPoolExecutor
    from multiprocessing import get_context

    from riftlab.profiles.domain import profiles_path

    _require_new_private(output)
    corpus = json.loads(manifest.read_bytes())
    if str(corpus.get("patch")) != patch:
        typer.echo("BLOCKED: manifesto de outro patch", err=True)
        raise typer.Exit(code=1)
    own_rules = _CONFIGS / "item-rules" / f"{patch}.json"
    rules_path = item_rules or (own_rules if own_rules.exists() else _CONFIGS / "item-rules")
    profile_path = profiles_path(_CONFIGS, patch)
    if not profile_path.exists():
        typer.echo("BLOCKED: perfis do patch ausentes (cc late profiles)", err=True)
        raise typer.Exit(code=1)
    if rules_path.is_dir() and source_item_json is None:
        typer.echo("BLOCKED: patch sem regras próprias exige --source-item-json", err=True)
        raise typer.Exit(code=1)
    pairs = [
        (split, match_id)
        for split in ("train", "validation", "test")
        for match_id in corpus["split"].get(f"{split}MatchIds", [])
    ]
    if limit:
        pairs = pairs[:limit]
    config = {
        "itemJson": str(item_json),
        "patch": patch,
        "dataDragonVersion": data_dragon_version,
        "empiricalRules": str(_CONFIGS / "empirical-rules.yaml"),
        "itemRules": str(rules_path),
        "sourceItemJson": str(source_item_json) if source_item_json else "",
        "profiles": str(profile_path),
        "rawRoots": [str(p) for p in raw_root],
    }
    counts: Counter[str] = Counter()
    started = time.monotonic()

    def rows() -> Iterator[dict[str, Any]]:
        with ProcessPoolExecutor(
            max_workers=workers,
            mp_context=get_context("fork"),
            initializer=_init_worker,
            initargs=(config,),
        ) as pool:
            for done, (found, diag) in enumerate(pool.map(_extract, pairs, chunksize=8), 1):
                counts.update(diag)
                counts["ROWS"] += len(found)
                yield from found
                if done % 500 == 0:
                    typer.echo(
                        json.dumps(
                            {
                                "processed": done,
                                "rows": counts["ROWS"],
                                "seconds": round(time.monotonic() - started),
                            }
                        ),
                        err=True,
                    )

    digest = write_jsonl_gz(output, rows())
    inputs = {
        str(p): hashlib.sha256(Path(p).read_bytes()).hexdigest()
        for p in (manifest, item_json, profile_path, _CONFIGS / "empirical-rules.yaml")
    }
    report = {
        "schemaVersion": "late_rows_v2",
        "patch": patch,
        "processed": len(pairs),
        "partial": bool(limit),
        "seconds": round(time.monotonic() - started, 1),
        "rowsSha256": digest,
        "inputs": inputs,
        "counts": dict(sorted(counts.items())),
    }
    output.with_name(output.name.removesuffix(".jsonl.gz") + ".report.json").write_text(
        json.dumps(report, indent=1, sort_keys=True) + "\n"
    )
    typer.echo(json.dumps({"rows": counts["ROWS"], "sha256": digest, "seconds": report["seconds"]}))


def _intern_categories(matrix: Any) -> None:
    import sys

    flat = matrix.categorical.reshape(-1)
    for i, value in enumerate(flat):
        flat[i] = sys.intern(value)


def _load_split_matrices(
    rows_path: Path, prior: Any, space: Any, top_k: int, splits: tuple[str, ...]
) -> dict[str, tuple[Any, list[dict[str, Any]]]]:
    """Uma passada: por divisão, a matriz (alvo fora do conjunto fica, conta como erro) e os
    metadados mínimos de cada decisão. Linhas inteiras nunca ficam todas na memória."""
    import numpy as np

    from riftlab.ml.late_model import Matrix, build_matrix

    parts: dict[str, list[Any]] = {s: [] for s in splits}
    metas: dict[str, list[dict[str, Any]]] = {s: [] for s in splits}
    chunk: dict[str, list[dict[str, Any]]] = {s: [] for s in splits}

    def flush(split: str) -> None:
        if not chunk[split]:
            return
        matrix = build_matrix(chunk[split], prior, space, top_k, require_target=False)
        _intern_categories(matrix)
        offset = len(metas[split])
        for index, ids, q in matrix.decisions:
            row = chunk[split][index]
            metas[split].append(
                {
                    "matchId": row["matchId"],
                    "championId": row["championId"],
                    "role": row.get("role"),
                    "ordinal": row["ordinal"],
                    "targetItemId": row["targetItemId"],
                    "targetLegal": row["targetLegal"],
                    "completion": {"kind": (row.get("completion") or {}).get("kind")},
                    "ids": ids,
                    "base": q,
                }
            )
        matrix.groups = matrix.groups + offset
        parts[split].append(matrix)
        chunk[split] = []

    for row in read_jsonl_gz(rows_path):
        split = row["split"]
        if split in chunk:
            chunk[split].append(row)
            if len(chunk[split]) >= 2000:
                flush(split)
    out: dict[str, tuple[Any, list[dict[str, Any]]]] = {}
    for split in splits:
        flush(split)
        blocks = parts[split]
        merged = Matrix(
            np.vstack([b.numeric for b in blocks]),
            np.vstack([b.categorical for b in blocks]),
            np.concatenate([b.labels for b in blocks]),
            np.concatenate([b.groups for b in blocks]),
            np.concatenate([b.group_weights for b in blocks]),
            [],
        )
        out[split] = (merged, metas[split])
    return out


def _subset_with_target(matrix: Any) -> Any:
    """Só as decisões cujo alvo está no conjunto plausível (treino e parada antecipada)."""
    import numpy as np

    from riftlab.ml.late_model import Matrix

    has = np.zeros(int(matrix.groups.max()) + 1, dtype=bool)
    np.logical_or.at(has, matrix.groups, matrix.labels > 0)
    keep = has[matrix.groups]
    _, new_groups = np.unique(matrix.groups[keep], return_inverse=True)
    return Matrix(
        matrix.numeric[keep],
        matrix.categorical[keep],
        matrix.labels[keep],
        new_groups.astype(np.int64),
        matrix.group_weights[has],
        [],
    )


def _scored(model: Any, matrix: Any, metas: list[dict[str, Any]], space: Any) -> list[Any]:
    import numpy as np

    from riftlab.ml.late_eval import Scored
    from riftlab.ml.late_model import make_pool, softmax_by_group

    sizes = [len(m["ids"]) for m in metas]
    if model is None:
        probs = [m["base"] for m in metas]
    else:
        raw = np.asarray(model.predict(make_pool(matrix, space, with_labels=False)))
        probs = softmax_by_group(raw, sizes)
    return [Scored(m, m["ids"], m["base"], p) for m, p in zip(metas, probs, strict=True)]


def _catalog_prices(item_json: Path) -> dict[int, int]:
    from riftlab.shared.numeric import is_ascii_digits

    document = json.loads(item_json.read_bytes())
    return {
        int(k): int(v["gold"]["total"])
        for k, v in document["data"].items()
        if is_ascii_digits(str(k)) and isinstance(v.get("gold"), dict)
    }


@late_app.command("train")
def late_train(
    rows_path: Path = typer.Option(..., "--rows", help="Linhas privadas (.jsonl.gz)."),
    item_json: Path = typer.Option(..., help="item.json do Data Dragon do patch (preços)."),
    patch: str = typer.Option(..., help="Patch das linhas."),
    output_dir: Path = typer.Option(..., help="Diretório privado novo do artefato."),
    top_k: int = typer.Option(30, min=5, max=80),
    depth: str = typer.Option("6", help="Profundidades da grade, separadas por vírgula."),
    learning_rate: float = typer.Option(0.1),
    iterations: int = typer.Option(1500),
    threads: int = typer.Option(5),
) -> None:
    """Padrão hierárquico + CatBoost (softmax por decisão) escolhido na validação. O teste não
    é lido aqui."""
    from catboost import CatBoost

    from riftlab.ml.late_eval import evaluate
    from riftlab.ml.late_model import (
        LATE_MODEL_VERSION,
        FeatureSpace,
        LateArtifact,
        LatePrior,
        context_columns,
        make_pool,
    )
    from riftlab.profiles.domain import load_profiles

    _require_new_private(output_dir)
    started = time.monotonic()
    prior = LatePrior.fit(r for r in read_jsonl_gz(rows_path) if r["split"] == "train")
    first = next(read_jsonl_gz(rows_path))
    profiles = load_profiles(_CONFIGS, patch)
    if profiles is None:
        typer.echo("BLOCKED: perfis do patch ausentes", err=True)
        raise typer.Exit(code=1)
    space = FeatureSpace.build(
        context_columns(first["context"]), profiles.items, _catalog_prices(item_json)
    )
    data = _load_split_matrices(rows_path, prior, space, top_k, ("train", "validation"))
    train_matrix, _ = data["train"]
    val_matrix, val_meta = data["validation"]
    train_pool = make_pool(_subset_with_target(train_matrix), space)
    val_pool = make_pool(_subset_with_target(val_matrix), space)
    typer.echo(
        json.dumps(
            {
                "trainRows": int(train_matrix.numeric.shape[0]),
                "features": len(space.numeric_names) + len(space.categorical_names),
                "loadSeconds": round(time.monotonic() - started),
            }
        ),
        err=True,
    )
    grid = []
    best: tuple[float, Any, dict[str, Any]] | None = None
    for d in (int(x) for x in depth.split(",")):
        params = {
            "loss_function": "QuerySoftMax",
            "eval_metric": "QuerySoftMax",
            "iterations": iterations,
            "learning_rate": learning_rate,
            "depth": d,
            "od_type": "Iter",
            "od_wait": 60,
            "random_seed": 1503,
            "thread_count": threads,
            "verbose": 50,
            "allow_writing_files": False,
            # Sem combinações de categóricas (caras em 2 milhões de linhas) e 64 faixas por
            # atributo: o contexto já entra explícito nas features de confronto.
            "max_ctr_complexity": 1,
            "border_count": 64,
        }
        t0 = time.monotonic()
        model = CatBoost(params)
        model.fit(train_pool, eval_set=val_pool, use_best_model=True, log_cout=sys.stderr)
        score = float(model.get_best_score()["validation"]["QuerySoftMax"])
        grid.append(
            {
                "depth": d,
                "learningRate": learning_rate,
                "trees": model.tree_count_,
                "validationQuerySoftMax": score,
                "seconds": round(time.monotonic() - t0),
            }
        )
        if best is None or score < best[0]:
            best = (score, model, params)
    assert best is not None
    model = best[1]
    report: dict[str, Any] = {
        "validation": evaluate(_scored(model, val_matrix, val_meta, space)),
        "grid": grid,
    }
    importance = sorted(
        zip(model.feature_names_, model.get_feature_importance(), strict=True),
        key=lambda kv: -kv[1],
    )[:40]
    metadata = {
        "version": LATE_MODEL_VERSION,
        "patch": patch,
        "rowsSha256": hashlib.sha256(rows_path.read_bytes()).hexdigest(),
        "params": {k: v for k, v in best[2].items() if k != "verbose"},
        "trainSeconds": round(time.monotonic() - started),
        "testOpened": False,
        "topFeatures": [[n, round(float(v), 3)] for n, v in importance],
    }
    LateArtifact(model, prior, space, top_k, metadata).save(output_dir)
    (output_dir / "validation-report.json").write_text(json.dumps(report, indent=1, sort_keys=True))
    summary = {
        name: report["validation"][name]["point"]
        for name in ("all", "deviation", "ordinal:5+", "deviation:ordinal:5+")
        if name in report["validation"]
    }
    typer.echo(
        json.dumps(
            {"grid": grid, "summary": summary, "claims": report["validation"]["deviationClaims"]},
            indent=1,
        )
    )


@late_app.command("evaluate-test")
def late_evaluate_test(
    rows_path: Path = typer.Option(..., "--rows"),
    item_json: Path = typer.Option(...),
    patch: str = typer.Option(...),
    artifact: Path = typer.Option(..., help="Diretório do artefato treinado."),
    preregistration: list[Path] = typer.Option(
        ..., help="Pré-registro do gate e emendas, cada um com .sha256 (repetível)."
    ),
) -> None:
    """Teste único: exige o pré-registro selado e grava a marca de teste aberto."""
    from riftlab.ml.late_eval import evaluate
    from riftlab.ml.late_model import LateArtifact
    from riftlab.profiles.domain import load_profiles

    marker = artifact / "TEST_OPENED"
    if marker.exists():
        typer.echo("BLOCKED: o teste deste artefato já foi aberto", err=True)
        raise typer.Exit(code=1)
    sealed: dict[str, str] = {}
    for document in preregistration:
        digest = document.with_name(document.name + ".sha256").read_text().split()[0]
        if hashlib.sha256(document.read_bytes()).hexdigest() != digest:
            typer.echo(f"BLOCKED: {document.name} diferente do selado", err=True)
            raise typer.Exit(code=1)
        sealed[document.name] = digest
    profiles = load_profiles(_CONFIGS, patch)
    assert profiles is not None
    loaded = LateArtifact.load(artifact, profiles.items, _catalog_prices(item_json))
    marker.write_text(json.dumps({"preregistrationSha256": sealed, "openedAt": time.time()}))
    data = _load_split_matrices(rows_path, loaded.prior, loaded.space, loaded.top_k, ("test",))
    matrix, meta = data["test"]
    report = evaluate(_scored(loaded.model, matrix, meta, loaded.space))
    (artifact / "test-report.json").write_text(json.dumps(report, indent=1, sort_keys=True))
    typer.echo(
        json.dumps(
            {k: report[k] for k in ("all", "deviation", "ordinal:5+") if k in report}, indent=1
        )
    )


@late_app.command("knowledge")
def late_knowledge(
    patch: str = typer.Option(..., help="Patch major.minor."),
    data_dragon_version: str = typer.Option(..., help="Versão do Data Dragon."),
    rows_path: Path = typer.Option(None, "--rows", help="Linhas de treino para os agregados."),
    cache: Path = typer.Option(_PRIVATE / "research" / "data-dragon", help="Cache privado."),
) -> None:
    """Base do RAG em configs/late/<patch>/: trechos pt_BR de itens e habilidades e agregados
    de situações parecidas (só treino, células com amostra mínima)."""
    from riftlab.dataset.late_corpus import catalog_from_data_dragon
    from riftlab.knowledge.base import build_cases, build_knowledge_document

    item_pt = _fetch_data_dragon(data_dragon_version, "pt_BR", "item", cache)
    champions_pt = _fetch_data_dragon(data_dragon_version, "pt_BR", "championFull", cache)
    item_en = _fetch_data_dragon(data_dragon_version, "en_US", "item", cache)
    bundle = catalog_from_data_dragon(
        json.loads(item_en), patch, data_dragon_version, hashlib.sha256(item_en).hexdigest()
    )
    target = _CONFIGS / "late" / patch
    target.mkdir(parents=True, exist_ok=True)
    document = build_knowledge_document(
        patch, data_dragon_version, item_pt, champions_pt, bundle.by_id.keys()
    )
    (target / "knowledge.json").write_text(
        json.dumps(document, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8"
    )
    summary: dict[str, Any] = {"chunks": len(document["chunks"])}
    if rows_path is not None:
        cases = build_cases(r for r in read_jsonl_gz(rows_path) if r["split"] == "train")
        cases["rowsSha256"] = hashlib.sha256(rows_path.read_bytes()).hexdigest()
        (target / "cases.json").write_text(json.dumps(cases, sort_keys=True) + "\n")
        summary.update(championCells=len(cases["champion"]), roleCells=len(cases["role"]))
    typer.echo(json.dumps(summary))


@late_app.command("publish")
def late_publish(
    artifact: Path = typer.Option(..., help="Diretório do artefato treinado e avaliado."),
    patch: str = typer.Option(...),
    artifacts_root: Path = typer.Option(
        _ROOT / "artifacts" / "late", help="Destino local (fora do Git) do model.cbm."
    ),
) -> None:
    """Versiona o padrão e o resultado do gate em configs/late/<patch>/ e copia o modelo para a
    pasta local montada na API. O gate vem do relatório do teste único (test-report.json)."""
    import shutil

    from riftlab.ml.late_gate import decide_gate

    metadata = json.loads((artifact / "metadata.json").read_text())
    report_path = artifact / "test-report.json"
    if not report_path.exists():
        typer.echo("BLOCKED: o teste único ainda não foi rodado", err=True)
        raise typer.Exit(code=1)
    gate = decide_gate(json.loads(report_path.read_text()))
    metadata["gate"] = gate
    target = _CONFIGS / "late" / patch
    target.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(artifact / "prior.json", target / "prior.json")
    (target / "metadata.json").write_text(json.dumps(metadata, indent=1, sort_keys=True) + "\n")
    local = artifacts_root / patch
    local.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(artifact / "model.cbm", local / "model.cbm")
    shutil.copyfile(artifact / "prior.json", local / "prior.json")
    (local / "metadata.json").write_text(json.dumps(metadata, indent=1, sort_keys=True) + "\n")
    typer.echo(json.dumps({"gate": gate, "configs": str(target), "model": str(local)}))
