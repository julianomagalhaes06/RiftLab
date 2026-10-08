from __future__ import annotations

from pathlib import Path
from typing import Any

import typer
import uvicorn
from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig

from riftlab.config import get_settings

app = typer.Typer(
    help="RiftLab CLI (cc). Comandos implementados nesta etapa: "
    "api, worker, health, db, riot, openapi, catalog, timeline, decisions, "
    "collect, report, matches.",
    no_args_is_help=True,
)

db_app = typer.Typer(help="Migrações (Alembic).", no_args_is_help=True)
app.add_typer(db_app, name="db")

openapi_app = typer.Typer(
    help="Contrato OpenAPI real, gerado da aplicacao (CC-106). Nunca inclui rota "
    "de sprint futura: so o que create_app() de fato monta.",
    no_args_is_help=True,
)
app.add_typer(openapi_app, name="openapi")

matches_app = typer.Typer(
    help="Operações locais explícitas sobre partidas importadas.", no_args_is_help=True
)
app.add_typer(matches_app, name="matches")


@matches_app.command("backfill-score")
def matches_backfill_score(
    match_id: str = typer.Option(..., help="ID da partida RIOT já importada."),
    raw_dir: Path = typer.Option(..., help="Diretório local imutável dos raw de importação."),
) -> None:
    """Completa placar legado só a partir de raw verificado, sem rede.

    Exige RIFTLAB_BACKFILL_DATABASE_URL explicitamente no ambiente. Nunca
    carrega `.env` ou usa RIFTLAB_DATABASE_URL implícito do app.
    """
    import asyncio
    import os
    import re

    from sqlalchemy.ext.asyncio import create_async_engine

    from riftlab.ingestion.score_backfill import ScoreBackfillConflict, backfill_match_score
    from riftlab.shared.db import build_session_factory

    if not re.fullmatch(r"BR1_[0-9]{1,20}", match_id):
        typer.echo("BLOCKED: match-id inválido.", err=True)
        raise typer.Exit(code=1)
    database_url = os.environ.get("RIFTLAB_BACKFILL_DATABASE_URL")
    if not database_url or not database_url.startswith("postgresql+asyncpg://"):
        typer.echo("BLOCKED: RIFTLAB_BACKFILL_DATABASE_URL explícita é obrigatória.", err=True)
        raise typer.Exit(code=1)

    async def run() -> None:
        engine = create_async_engine(database_url)
        try:
            result = await backfill_match_score(build_session_factory(engine), raw_dir, match_id)
            if result.status == "RAW_INVALID":
                typer.echo("BLOCKED: raw local inválido; nenhuma escrita realizada.", err=True)
                raise typer.Exit(code=1)
            typer.echo(
                f"{result.status}: participantes={result.participants} "
                f"campos_preenchidos={result.fields_filled}"
            )
        finally:
            await engine.dispose()

    try:
        asyncio.run(run())
    except ScoreBackfillConflict as exc:
        typer.echo(f"BLOCKED: {exc}", err=True)
        raise typer.Exit(code=1) from exc


@openapi_app.command("export")
def openapi_export(
    out: Path = typer.Argument(..., help="Caminho do arquivo JSON de saida."),
) -> None:
    """Exporta o schema OpenAPI real da aplicacao (riftlab.api.app.create_app)
    para um arquivo JSON, formatado de forma estavel (chaves ordenadas,
    indentacao de 2 espacos) para diff previsivel entre execucoes."""
    import json

    from riftlab.api.app import create_app

    schema = create_app().openapi()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
    typer.echo(f"OpenAPI exportado para {out}")


from riftlab.cli.collect import collect_app, report_app  # noqa: E402  (CC-206)

app.add_typer(collect_app, name="collect")
app.add_typer(report_app, name="report")

riot_app = typer.Typer(
    help="Spike administrativo de auditoria de acesso real (CC-105). Não é o "
    "importador durável do CC-201.",
    no_args_is_help=True,
)
app.add_typer(riot_app, name="riot")

catalog_app = typer.Typer(
    help="Sincronização de catálogo versionado e imutável (CC-202).",
    no_args_is_help=True,
)
app.add_typer(catalog_app, name="catalog")

patch_app = typer.Typer(help="Detecção explícita do patch vigente.", no_args_is_help=True)
app.add_typer(patch_app, name="patch")

dataset_app = typer.Typer(
    help="Dataset preliminar (CC-302): corpus congelado e divisão cronológica.",
    no_args_is_help=True,
)
app.add_typer(dataset_app, name="dataset")

_REPO_ROOT = Path(__file__).resolve().parents[4]


def _verified_private_json(path: Path) -> Any:
    import hashlib
    import json

    raw = path.read_bytes()
    sidecar = path.with_suffix(path.suffix + ".sha256").read_text(encoding="ascii")
    if sidecar != f"{hashlib.sha256(raw).hexdigest()}  {path.name}\n":
        raise typer.Exit(code=1)
    return json.loads(raw)


@dataset_app.command("build-rows")
def dataset_build_rows(
    manifest: Path = typer.Option(
        _REPO_ROOT / ".private" / "dataset" / "corpus-16.19.json", help="Manifesto congelado."
    ),
    rules: Path = typer.Option(
        _REPO_ROOT / "configs" / "item-rules" / "16.19.json", help="Regras de itens do patch."
    ),
    output: Path = typer.Option(
        _REPO_ROOT / ".private" / "dataset" / "rows-16.19.json", help="Saída privada única."
    ),
    env_file: Path | None = typer.Option(None, help="Banco preparado (descartável)."),
    task: str = typer.Option("purchase", help="purchase (CC-302) ou planning (CC-407)."),
) -> None:
    """Gera linhas decisão × candidatos legais a partir de um banco já preparado."""
    import asyncio
    import json

    from riftlab.candidates.rules import load_item_rules
    from riftlab.cli.env_file import load_settings
    from riftlab.collection.cc316_adoption import canonical_json
    from riftlab.collection.cc316_discovery import _atomic_write_once
    from riftlab.dataset.rows import build_rows
    from riftlab.db import build_engine
    from riftlab.shared.db import build_session_factory

    if ".private" not in output.parts or output.exists():
        typer.echo("BLOCKED: saída deve ser nova e ficar em .private", err=True)
        raise typer.Exit(code=1)
    corpus = _verified_private_json(manifest)
    item_rules = load_item_rules(rules)
    settings = load_settings(env_file)

    async def _run() -> Any:
        engine = build_engine(settings.database_url)
        try:
            return await build_rows(build_session_factory(engine), corpus, item_rules, task=task)
        finally:
            await engine.dispose()

    rows, counts = asyncio.run(_run())
    artifact = {
        "schemaVersion": 1,
        "card": "CC-302",
        "corpusSha256": manifest.with_suffix(manifest.suffix + ".sha256").read_text().split()[0],
        "rulesVersion": item_rules.version,
        "counts": dict(sorted(counts.items())),
        "rows": rows,
    }
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    digest = _atomic_write_once(output, canonical_json(artifact))
    typer.echo(json.dumps({"counts": artifact["counts"], "sha256": digest}, sort_keys=True))


ml_app = typer.Typer(
    help="Ranking contextual (CC-402): treino e teste único.", no_args_is_help=True
)
app.add_typer(ml_app, name="ml")
_CC402 = _REPO_ROOT / ".private" / "validation" / "sprint-4" / "CC-402" / "catalog"
_ITEMS_16_19 = (
    _REPO_ROOT
    / ".private"
    / "validation"
    / "sprint-3"
    / "CC-321"
    / "catalog"
    / "item-16.19.1-en_US.json"
)


@ml_app.command("train-ranker")
def ml_train_ranker(
    dataset: Path = typer.Option(..., help="Dataset final com candidatos enriquecidos."),
    run_dir: Path = typer.Option(..., help="Diretório novo da execução (artefatos privados)."),
    champions: Path = typer.Option(_CC402 / "champion-16.19.1-en_US.json"),
    items: Path = typer.Option(_ITEMS_16_19),
) -> None:
    """Seleciona B0/B1/M1 e ablações só na validação; o teste fica fechado."""
    import json

    from riftlab.ml.experiment import ExperimentError, train_run

    try:
        report = train_run(dataset, champions, items, run_dir)
    except ExperimentError as exc:
        typer.echo(f"BLOCKED: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    summary = {
        name: {"mrr": r["all"]["point"]["mrr"], "ci95": r["all"]["ci95"].get("mrr")}
        for name, r in report["validation"].items()
    }
    typer.echo(json.dumps({"validation": summary, "run": report["runSha256"]}, sort_keys=True))


@ml_app.command("evaluate-test")
def ml_evaluate_test(
    run_dir: Path = typer.Option(..., help="Execução já selecionada."),
    dataset: Path = typer.Option(..., help="Mesmo dataset da seleção."),
    champions: Path = typer.Option(_CC402 / "champion-16.19.1-en_US.json"),
    items: Path = typer.Option(_ITEMS_16_19),
) -> None:
    """Avalia uma única vez no teste (recusa segunda execução ou insumo alterado)."""
    import json

    from riftlab.ml.experiment import ExperimentError, test_run

    try:
        result = test_run(run_dir, dataset, champions, items)
    except ExperimentError as exc:
        typer.echo(f"BLOCKED: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(
        json.dumps({n: r["all"]["point"] for n, r in result["test"].items()}, sort_keys=True)
    )


@dataset_app.command("freeze-dataset")
def dataset_freeze_final(
    rows_path: Path = typer.Option(..., help="Linhas congeladas (JSON com sidecar)."),
    manifest: Path = typer.Option(..., help="Manifesto do corpus com treino/validação/teste."),
    output: Path = typer.Option(..., help="Dataset final privado, gravado uma vez."),
    collection_dir: Path = typer.Option(
        _REPO_ROOT / ".private" / "collection", help="Raiz privada das coletas (só leitura)."
    ),
) -> None:
    """Congela o dataset final (CC-401): rótulos da divisão, cobertura e contaminação."""
    import hashlib
    import json

    from riftlab.collection.cc316_adoption import canonical_json
    from riftlab.collection.cc316_discovery import _atomic_write_once
    from riftlab.dataset.final import build_final_dataset

    if ".private" not in output.parts or output.exists():
        typer.echo("BLOCKED: saída deve ser nova e ficar em .private", err=True)
        raise typer.Exit(code=1)
    rows_artifact = _verified_private_json(rows_path)
    corpus = _verified_private_json(manifest)
    artifact = build_final_dataset(
        rows_artifact,
        corpus,
        [
            collection_dir / "cc316" / "raw-exploration",
            collection_dir / "cc316" / "raw-confirmation",
            collection_dir / "cc323" / "raw",
        ],
        rows_sha256=hashlib.sha256(rows_path.read_bytes()).hexdigest(),
        manifest_sha256=hashlib.sha256(manifest.read_bytes()).hexdigest(),
    )
    digest = _atomic_write_once(output, canonical_json(artifact))
    summary = {key: artifact[key] for key in ("coverage", "playerContamination")}
    typer.echo(json.dumps({**summary, "sha256": digest}, sort_keys=True, ensure_ascii=False))


@dataset_app.command("baseline")
def dataset_baseline(
    rows_path: Path = typer.Option(
        _REPO_ROOT / ".private" / "dataset" / "rows-16.19.json", help="Linhas congeladas."
    ),
) -> None:
    """Ajusta o baseline de popularidade só no treino e avalia no teste (imitação)."""
    import json

    from riftlab.dataset.popularity import evaluate, fit

    artifact = _verified_private_json(rows_path)
    rows = artifact["rows"]
    model = fit(row for row in rows if row["split"] == "train")
    report = {
        "train": evaluate(model, [row for row in rows if row["split"] == "train"]),
        **{
            split: evaluate(model, [row for row in rows if row["split"] == split])
            for split in ("validation", "test")
            if any(row["split"] == split for row in rows)
        },
    }
    typer.echo(json.dumps(report, sort_keys=True, ensure_ascii=False))


@dataset_app.command("freeze-corpus")
def dataset_freeze_corpus(
    output: Path = typer.Option(
        _REPO_ROOT / ".private" / "dataset" / "corpus-16.19.json",
        help="Manifesto privado, gravado uma única vez com sidecar SHA-256.",
    ),
    policy: Path = typer.Option(
        _REPO_ROOT / "configs" / "dataset.yaml", help="Política pública do dataset."
    ),
    collection_dir: Path = typer.Option(
        _REPO_ROOT / ".private" / "collection", help="Raiz privada das coletas."
    ),
) -> None:
    """Congela o corpus do patch (CC-316 + CC-323) e a divisão treino/teste, sem rede nem banco."""
    import json

    from riftlab.dataset.corpus import CorpusError, freeze_corpus

    sources = {
        "cc316-exploration": collection_dir / "cc316" / "raw-exploration",
        "cc316-confirmation": collection_dir / "cc316" / "raw-confirmation",
        "cc323": collection_dir / "cc323" / "raw",
    }
    try:
        manifest, digest = freeze_corpus(output, sources, policy)
    except CorpusError as exc:
        typer.echo(f"BLOCKED: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    split = manifest["split"]
    assert isinstance(split, dict)
    typer.echo(
        json.dumps(
            {
                "counts": manifest["counts"],
                "sources": manifest["sources"],
                "boundaryGameStartMs": split["boundaryGameStartMs"],
                "sha256": digest,
            },
            sort_keys=True,
        )
    )


@patch_app.command("detect")
def patch_detect(
    env_file: Path = typer.Option(None, help="Arquivo de ambiente do banco alvo."),
) -> None:
    """Lê versions.json público e registra o primeiro patch da lista no banco configurado."""
    import asyncio

    from riftlab.catalog.infrastructure.data_dragon_transport import DataDragonTransport
    from riftlab.catalog.patches import record_current_patch
    from riftlab.cli.env_file import load_settings
    from riftlab.db import build_engine
    from riftlab.shared.db import build_session_factory
    from riftlab.shared.errors import SourceError

    settings = load_settings(env_file)

    async def run() -> str:
        engine = build_engine(settings.database_url)
        transport = DataDragonTransport()
        try:
            raw = await transport.fetch_versions()
            return await record_current_patch(build_session_factory(engine), raw.body)
        finally:
            await transport.aclose()
            await engine.dispose()

    try:
        typer.echo(f"patchVigente={asyncio.run(run())}")
    except SourceError as exc:
        typer.echo(f"BLOCKED: {exc.code}", err=True)
        raise typer.Exit(code=1) from exc


@catalog_app.command("sync")
def catalog_sync(
    mode: str = typer.Option(
        ..., help="'demo' (fixture sintética, sem rede) ou 'real' (Data Dragon público)."
    ),
    patch: str = typer.Option(
        ...,
        help="Identificador do catálogo: id da fixture demo (ex.: DEMO-1) ou "
        "gamePatch major.minor real (ex.: 16.17, precisa estar em "
        "configs/catalog-mappings.yaml).",
    ),
    mapping_file: Path = typer.Option(
        None,
        help="Caminho do mapping YAML (padrão: RIFTLAB_CATALOG_MAPPINGS_PATH/"
        "Settings.catalog_mappings_path). Só usado em --mode real.",
    ),
    env_file: Path = typer.Option(
        None, help="Caminho do .env a carregar (padrão: raiz do repositório)."
    ),
) -> None:
    """Busca (rede fora de transação em modo real), valida e persiste
    catálogo+itens numa transação única. Idempotente: mesmo conteúdo
    (raw+mapping+curadoria) nunca duplica linha, sempre devolve a mesma
    identidade. Modo demo nunca precisa de rede nem banco configurado além
    do já exigido para qualquer comando desta CLI."""
    import asyncio

    from riftlab.catalog.application import sync_catalog
    from riftlab.catalog.infrastructure.demo_source import DemoCatalogSource
    from riftlab.cli.env_file import load_settings
    from riftlab.db import build_engine
    from riftlab.shared.db import build_session_factory
    from riftlab.shared.errors import SourceError

    if mode not in ("demo", "real"):
        typer.echo(f"BLOCKED: --mode deve ser 'demo' ou 'real', recebido {mode!r}.", err=True)
        raise typer.Exit(code=1)

    settings = load_settings(env_file)

    async def _run() -> None:
        from riftlab.catalog.domain import CatalogSource

        engine = build_engine(settings.database_url)
        session_factory = build_session_factory(engine)
        transport = None
        try:
            source: CatalogSource
            if mode == "demo":
                source = DemoCatalogSource()
            else:
                from riftlab.catalog.infrastructure.catalog_mapping import load_catalog_mapping
                from riftlab.catalog.infrastructure.data_dragon_source import (
                    DataDragonCatalogSource,
                )
                from riftlab.catalog.infrastructure.data_dragon_transport import (
                    DataDragonTransport,
                )

                resolved_mapping_file = mapping_file or Path(settings.catalog_mappings_path)
                if not resolved_mapping_file.exists():
                    typer.echo(
                        f"BLOCKED: mapping de catálogo não encontrado em {resolved_mapping_file}.",
                        err=True,
                    )
                    raise typer.Exit(code=1)
                transport = DataDragonTransport()
                from riftlab.catalog.patches import ensure_patch_mapping

                mapping = await ensure_patch_mapping(
                    session_factory, load_catalog_mapping(resolved_mapping_file), patch, transport
                )
                source = DataDragonCatalogSource(transport, mapping)

            result = await sync_catalog(session_factory, source, patch)
            typer.echo(
                f"catalogId={result.id} gamePatch={result.game_patch} "
                f"dataDragonVersion={result.data_dragon_version} sha256={result.sha256}"
            )
        finally:
            if transport is not None:
                await transport.aclose()
            await engine.dispose()

    try:
        asyncio.run(_run())
    except SourceError as exc:
        typer.echo(f"BLOCKED: {exc.code} - {exc}", err=True)
        raise typer.Exit(code=1) from exc


@catalog_app.command("sync-champions")
def catalog_sync_champions(
    patch: str = typer.Option(
        ..., help="gamePatch major.minor real (ex.: 16.17), com entrada no mapping de catálogo."
    ),
    mapping_file: Path = typer.Option(
        None,
        help="Caminho do mapping YAML (padrão: RIFTLAB_CATALOG_MAPPINGS_PATH/"
        "Settings.catalog_mappings_path).",
    ),
    env_file: Path = typer.Option(
        None, help="Caminho do .env a carregar (padrão: raiz do repositório)."
    ),
) -> None:
    """Nomes e retratos oficiais dos campeões (CC-208): busca champion.json pt_BR da
    versão do Data Dragon FIXADA para o patch e grava o catálogo. Patch sem mapping não
    usa rede; catálogo já gravado não é buscado de novo (idempotente)."""
    import asyncio

    from riftlab.catalog.infrastructure.catalog_mapping import load_catalog_mapping
    from riftlab.catalog.infrastructure.data_dragon_transport import DataDragonTransport
    from riftlab.champions.application import ensure_champion_catalog
    from riftlab.cli.env_file import load_settings
    from riftlab.db import build_engine
    from riftlab.shared.db import build_session_factory
    from riftlab.shared.errors import SourceError

    settings = load_settings(env_file)
    resolved_mapping_file = mapping_file or Path(settings.catalog_mappings_path)
    if not resolved_mapping_file.exists():
        typer.echo(
            f"BLOCKED: mapping de catálogo não encontrado em {resolved_mapping_file}.", err=True
        )
        raise typer.Exit(code=1)
    mapping = load_catalog_mapping(resolved_mapping_file)

    async def _run() -> str:
        engine = build_engine(settings.database_url)
        transport = DataDragonTransport()
        try:
            from riftlab.catalog.patches import ensure_patch_mapping

            resolved = await ensure_patch_mapping(
                build_session_factory(engine), mapping, patch, transport
            )
            outcome = await ensure_champion_catalog(
                build_session_factory(engine), resolved, patch, transport.fetch_champion_catalog
            )
            return outcome.value
        finally:
            await transport.aclose()
            await engine.dispose()

    try:
        outcome = asyncio.run(_run())
    except SourceError as exc:
        typer.echo(f"BLOCKED: {exc.code} - {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"patch={patch} resultado={outcome}")
    if outcome == "NO_MAPPING":
        raise typer.Exit(code=1)


timeline_app = typer.Typer(
    help="Reconstrução pré-compra de inventário (CC-203).",
    no_args_is_help=True,
)
app.add_typer(timeline_app, name="timeline")


@timeline_app.command("reconstruct")
def timeline_reconstruct(
    mode: str = typer.Option(
        ..., help="'demo' (fixture sintética, sem rede) ou 'real' (raw já importado, CC-201)."
    ),
    match_id: str = typer.Option(
        ..., help="Identificador da partida já importada (POST /imports)."
    ),
    mapping_file: Path = typer.Option(
        None,
        help="Caminho do mapping YAML de catálogo (padrão: "
        "RIFTLAB_CATALOG_MAPPINGS_PATH/Settings.catalog_mappings_path). "
        "Só usado em --mode real; --mode demo usa o mapping fixo do catálogo demo (DEMO-1).",
    ),
    env_file: Path = typer.Option(
        None, help="Caminho do .env a carregar (padrão: raiz do repositório)."
    ),
) -> None:
    """Busca (banco + fonte local, fora de transação) -> reduz (puro,
    domain-model.md) -> persiste (uma transação, todos os participantes
    juntos ou nada). Idempotente: partida já reconstruída não duplica.
    Nunca coleta partida nova — a fonte só re-lê dado já importado (CC-201)."""
    import asyncio

    from riftlab.catalog.infrastructure.catalog_mapping import load_catalog_mapping
    from riftlab.catalog.infrastructure.demo_source import demo_catalog_mapping
    from riftlab.cli.env_file import load_settings
    from riftlab.db import build_engine
    from riftlab.ingestion.domain import MatchSource
    from riftlab.ingestion.infrastructure.demo_source import DemoMatchSource
    from riftlab.shared.db import build_session_factory
    from riftlab.shared.errors import DomainError, SourceError
    from riftlab.timeline.application import RECONSTRUCTION_ALGORITHM_VERSION, reconstruct_match
    from riftlab.timeline.empirical_rules import load_empirical_rules_or_disabled

    if mode not in ("demo", "real"):
        typer.echo(f"BLOCKED: --mode deve ser 'demo' ou 'real', recebido {mode!r}.", err=True)
        raise typer.Exit(code=1)

    settings = load_settings(env_file)
    if mode == "demo":
        # Catálogo demo (fixtures/catalogs/demo_1.json, CC-202) não tem
        # entrada no mapping YAML real (esse só cobre patches reais de
        # configs/catalog-mappings.yaml) — derivado da própria fixture.
        mapping = demo_catalog_mapping()
    else:
        resolved_mapping_file = mapping_file or Path(settings.catalog_mappings_path)
        if not resolved_mapping_file.exists():
            typer.echo(
                f"BLOCKED: mapping de catálogo não encontrado em {resolved_mapping_file}.", err=True
            )
            raise typer.Exit(code=1)
        mapping = load_catalog_mapping(resolved_mapping_file)

    async def _run() -> None:
        from riftlab.ingestion.infrastructure.raw_store import DurableRawMatchStore
        from riftlab.ingestion.infrastructure.riot_source import LocalRawMatchSource

        engine = build_engine(settings.database_url)
        session_factory = build_session_factory(engine)
        try:
            effective_mapping = mapping
            if mode == "real":
                from riftlab.catalog.patches import load_persisted_mapping

                effective_mapping = await load_persisted_mapping(session_factory, mapping)
            source: MatchSource
            if mode == "demo":
                source = DemoMatchSource()
            else:
                # Reconstrução nunca coleta partida nova (fronteira explícita
                # do plano): LocalRawMatchSource não importa transporte/chave
                # nenhum, estruturalmente incapaz de emitir rede — falha
                # explícita se o raw ainda não estiver em cache local, nunca
                # busca da Riot para completar (achado da revisão de aa3e304).
                raw_store = DurableRawMatchStore(Path(settings.raw_storage_dir))
                source = LocalRawMatchSource(raw_store, settings.subject_hash_secret)

            rules = load_empirical_rules_or_disabled(
                Path(settings.empirical_rules_path),
                reconstruction_version=RECONSTRUCTION_ALGORITHM_VERSION,
            )
            result = await reconstruct_match(
                session_factory, effective_mapping, source, match_id, empirical_rules=rules
            )
            typer.echo(
                f"matchId={result.match_id} catalogId={result.catalog_id} "
                f"decisionStates={len(result.decision_states)}"
            )
        finally:
            await engine.dispose()

    try:
        asyncio.run(_run())
    except (SourceError, DomainError) as exc:
        code = getattr(exc, "code", exc.__class__.__name__)
        typer.echo(f"BLOCKED: {code} - {exc}", err=True)
        raise typer.Exit(code=1) from exc


decisions_app = typer.Typer(
    help="Extração de pontos de decisão e features (CC-204).",
    no_args_is_help=True,
)
app.add_typer(decisions_app, name="decisions")


@decisions_app.command("extract")
def decisions_extract(
    match_id: str = typer.Option(
        ..., help="Identificador da partida já importada E reconstruída (versão corrente)."
    ),
    scope_file: Path = typer.Option(
        None,
        help="Caminho do recorte real (padrão: RIFTLAB_SCOPE_PATH/Settings.scope_path). "
        "Só lido para dados de origem real; a origem demo usa o recorte sintético.",
    ),
    env_file: Path = typer.Option(
        None, help="Caminho do .env a carregar (padrão: raiz do repositório)."
    ),
) -> None:
    """Lê os estados já reconstruídos (só banco: nunca reconstrói, abre fonte
    nem acessa rede), seleciona os pontos de decisão, avalia elegibilidade e
    features e grava marca + pontos numa transação. Idempotente por partida e
    tupla de versões. Fluxo local completo: `cc catalog sync` -> importação
    (POST /api/v1/imports) -> `cc timeline reconstruct` -> `cc decisions
    extract`. A saída é um resumo sem dado pessoal."""
    import asyncio
    import json

    from riftlab.cli.env_file import load_settings
    from riftlab.db import build_engine
    from riftlab.decisions.application import extract_decisions
    from riftlab.shared.db import build_session_factory
    from riftlab.shared.errors import DomainError

    settings = load_settings(env_file)
    resolved_scope_file = scope_file or Path(settings.scope_path)

    async def _run() -> None:
        engine = build_engine(settings.database_url)
        session_factory = build_session_factory(engine)
        try:
            extraction = await extract_decisions(
                session_factory, match_id, scope_path=resolved_scope_file
            )
            typer.echo(json.dumps(extraction.summary(), indent=2, sort_keys=True))
        finally:
            await engine.dispose()

    try:
        asyncio.run(_run())
    except DomainError as exc:
        code = getattr(exc, "code", exc.__class__.__name__)
        typer.echo(f"BLOCKED: {code} - {exc}", err=True)
        raise typer.Exit(code=1) from exc


def _alembic_config() -> AlembicConfig:
    # alembic.ini fica na raiz do pacote backend; cc e alembic.ini compartilham cwd
    # tanto em desenvolvimento (uv run, a partir de backend/) quanto na imagem
    # (WORKDIR /app).
    return AlembicConfig("alembic.ini")


@db_app.command("upgrade")
def db_upgrade(
    revision: str = typer.Argument("head", help="Revisão alvo (padrão: head)."),
) -> None:
    """Aplica migrations até a revisão informada. Idempotente em head."""
    alembic_command.upgrade(_alembic_config(), revision)


@db_app.command("current")
def db_current() -> None:
    """Mostra a revisão atual do banco configurado (RIFTLAB_DATABASE_URL)."""
    alembic_command.current(_alembic_config(), verbose=True)


@riot_app.command("audit-sample")
def riot_audit_sample(
    seeds: Path = typer.Option(
        None,
        help="Arquivo com um match_id BR1 por linha (sementes já obtidas). "
        "Mutuamente exclusivo com --account-file.",
    ),
    account_file: Path = typer.Option(
        None,
        help="Arquivo local de conta (padrão: .private/riot-account.json) para "
        "descobrir só as próprias partidas concluídas via Account-V1/Match-V5. "
        "Mutuamente exclusivo com --seeds.",
    ),
    env_file: Path = typer.Option(
        None, help="Caminho do .env a carregar (padrão: raiz do repositório)."
    ),
    out_dir: Path = typer.Option(
        None,
        help="Diretório local git-ignorado para corpos brutos e manifesto de retomada "
        "(padrão: <raiz do repositório>/data/raw/cc-105, absoluto — nunca relativo ao "
        "cwd de onde `cc` foi chamado).",
    ),
    max_matches: int = typer.Option(50, help="Teto de partidas buscadas nesta execução."),
    max_requests: int = typer.Option(
        200,
        help="Teto de requisições HTTP para todo o manifesto (compartilhado entre "
        "execuções via requestsUsedTotal — uma execução pequena depois de outra não "
        "reinicia o orçamento).",
    ),
    queue: int = typer.Option(
        420, help="Fila usada na descoberta por conta (ranqueada solo/duo, docs/evaluation.md)."
    ),
) -> None:
    """Busca sequencialmente partida+timeline de cada semente (de --seeds ou
    descobertas só da própria conta via --account-file), respeitando orçamento
    local compartilhado e Retry-After real, sem persistir em banco nem
    reconstruir estado (isso é CC-201/203/204). Nunca imprime chave, Riot ID ou
    PUUID; encerra com BLOCKED se RIFTLAB_RIOT_API_KEY e ao menos uma origem de
    sementes não estiverem disponíveis (modo demo não os exige)."""
    import asyncio

    from riftlab.cli.env_file import load_settings
    from riftlab.ingestion.infrastructure.raw_storage import DEFAULT_OUT_DIR, RawMatchStorage
    from riftlab.ingestion.infrastructure.riot_account import (
        DEFAULT_ACCOUNT_FILE,
        RiotAccountConfig,
        RiotAccountConfigError,
        load_riot_account,
    )
    from riftlab.ingestion.infrastructure.riot_audit import (
        AuditSampleResult,
        discover_seed_match_ids,
        run_audit_sample,
    )
    from riftlab.ingestion.infrastructure.riot_transport import RiotFetchBudget, RiotHttpTransport
    from riftlab.shared.errors import SourceError

    settings = load_settings(env_file)
    if settings.riot_api_key is None:
        typer.echo(
            "BLOCKED: RIFTLAB_RIOT_API_KEY nao configurada (nem no ambiente, nem no "
            ".env resolvido); auditoria real exige credencial fora do Git. Nada foi buscado.",
            err=True,
        )
        raise typer.Exit(code=1)
    if seeds is not None and account_file is not None:
        typer.echo(
            "BLOCKED: --seeds e --account-file sao mutuamente exclusivos nesta execucao.",
            err=True,
        )
        raise typer.Exit(code=1)

    match_ids: list[str] | None = None
    account: RiotAccountConfig | None = None

    if seeds is not None:
        match_ids = [
            line.strip()
            for line in seeds.read_text().splitlines()
            if line.strip() and not line.startswith("#")
        ]
        if not match_ids:
            typer.echo(f"BLOCKED: nenhuma semente em {seeds}.", err=True)
            raise typer.Exit(code=1)
    else:
        resolved_account_file = account_file or DEFAULT_ACCOUNT_FILE
        if not resolved_account_file.exists() and account_file is None:
            typer.echo(
                "BLOCKED: nem --seeds nem --account-file (nem o padrao "
                f"{DEFAULT_ACCOUNT_FILE}) estao disponiveis. Nada foi buscado.",
                err=True,
            )
            raise typer.Exit(code=1)
        try:
            account = load_riot_account(resolved_account_file)
        except RiotAccountConfigError as exc:
            typer.echo(f"BLOCKED: {exc}", err=True)
            raise typer.Exit(code=1) from exc

    resolved_out_dir = out_dir or DEFAULT_OUT_DIR
    storage = RawMatchStorage(resolved_out_dir)
    already_used = storage.requests_used_total()
    remaining_requests = max_requests - already_used
    if remaining_requests <= 0:
        typer.echo(
            f"BLOCKED: orcamento de requisicoes (max_requests={max_requests}) ja foi "
            f"consumido por execucoes anteriores deste manifesto ({already_used} usadas "
            f"em {resolved_out_dir}). Aumente --max-requests conscientemente para continuar.",
            err=True,
        )
        raise typer.Exit(code=1)

    budget = RiotFetchBudget(max_matches=max_matches, max_requests=remaining_requests)

    async def _run() -> AuditSampleResult | None:
        transport = RiotHttpTransport(api_key=settings.riot_api_key, budget=budget)  # type: ignore[arg-type]
        try:
            local_match_ids = match_ids
            expected_puuid: str | None = None
            if account is not None:
                expected_puuid, local_match_ids = await discover_seed_match_ids(
                    transport, account, queue=queue, max_seeds=max_matches
                )
            if not local_match_ids:
                return None
            return await run_audit_sample(
                transport, storage, local_match_ids, budget, expected_puuid=expected_puuid
            )
        finally:
            storage.record_requests_used(transport.requests_made)
            await transport.aclose()

    try:
        result = asyncio.run(_run())
    except SourceError as exc:
        typer.echo(f"BLOCKED: {exc.code} - {exc}", err=True)
        raise typer.Exit(code=1) from exc

    if result is None:
        typer.echo("descoberta: nenhuma partida encontrada para a conta/fila configurada.")
        raise typer.Exit(code=0)

    typer.echo(
        f"buscadas={len(result.fetched)} cache={len(result.skipped_cached)} "
        f"excluidas={len(result.excluded)} parada_antecipada={result.stopped_early_reason}"
    )


@app.command()
def api(
    host: str = typer.Option(None, help="Sobrescreve RIFTLAB_API_HOST."),
    port: int = typer.Option(None, help="Sobrescreve RIFTLAB_API_PORT."),
    reload: bool = typer.Option(False, help="Recarrega em mudancas de codigo (desenvolvimento)."),
) -> None:
    """Inicia a API FastAPI (uvicorn), health checks e nada mais nesta etapa."""
    settings = get_settings()
    uvicorn.run(
        "riftlab.api.main:app",
        host=host or settings.api_host,
        port=port or settings.api_port,
        reload=reload,
        log_config=None,
    )


@app.command()
def worker() -> None:
    """Inicia o worker minimo com heartbeat verificavel; sem processamento de fila."""
    from riftlab.worker.main import main as worker_main

    worker_main()


@app.command()
def health(
    host: str = typer.Option("127.0.0.1", help="Host da API a consultar."),
    port: int = typer.Option(None, help="Porta da API; usa RIFTLAB_API_PORT por padrao."),
    timeout: float = typer.Option(3.0, help="Timeout em segundos por chamada."),
) -> None:
    """Consulta /health/live e /health/ready localmente; saida != 0 se alguma falhar."""
    import httpx

    settings = get_settings()
    resolved_port = port or settings.api_port
    exit_code = 0
    for path in ("/health/live", "/health/ready"):
        url = f"http://{host}:{resolved_port}{path}"
        try:
            response = httpx.get(url, timeout=timeout)
            ok = response.status_code == 200
            detail = str(response.status_code)
        except httpx.HTTPError as exc:
            ok = False
            detail = f"sem resposta ({exc.__class__.__name__})"
        typer.echo(f"{path}: {'OK' if ok else 'FAIL'} ({detail})")
        if not ok:
            exit_code = 1
    raise typer.Exit(code=exit_code)


if __name__ == "__main__":
    app()


ranked_app = typer.Typer(help="Atualização explícita da posição da conta local.")
app.add_typer(ranked_app, name="ranked")


@ranked_app.command("refresh")
def ranked_refresh(
    season: str = typer.Option(..., help="Temporada explícita, ex.: 2026 ou 2026-3."),
    account_file: Path = typer.Option(None, help="Arquivo privado da conta local."),
    env_file: Path = typer.Option(None, help="Arquivo .env local; valores nunca impressos."),
) -> None:
    import asyncio

    from riftlab.cli.env_file import load_settings
    from riftlab.db import build_engine
    from riftlab.ingestion.infrastructure.riot_account import RiotAccountConfigError
    from riftlab.ranked.application import refresh_ranked
    from riftlab.shared.db import build_session_factory
    from riftlab.shared.errors import SourceError

    settings = load_settings(env_file)
    if account_file is not None:
        settings.ranked_account_file = str(account_file)

    async def _run() -> dict[str, str]:
        engine = build_engine(settings.database_url)
        try:
            return await refresh_ranked(build_session_factory(engine), settings, season)
        finally:
            await engine.dispose()

    try:
        result = asyncio.run(_run())
    except (SourceError, ValueError, RiotAccountConfigError, OSError) as exc:
        typer.echo(f"BLOCKED: {getattr(exc, 'code', type(exc).__name__)}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"estado={result['state']} fila={result['queue']} temporada={result['season']}")
