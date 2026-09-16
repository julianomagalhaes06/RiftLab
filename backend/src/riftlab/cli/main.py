from __future__ import annotations

from pathlib import Path

import typer
import uvicorn
from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig

from riftlab.config import get_settings

app = typer.Typer(
    help="RiftLab CLI (cc). Comandos implementados nesta etapa: "
    "api, worker, health, db, riot, openapi, catalog, timeline.",
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
                mapping = load_catalog_mapping(resolved_mapping_file)
                transport = DataDragonTransport()
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
    from riftlab.timeline.application import reconstruct_match

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
        from riftlab.ingestion.infrastructure.riot_source import RiotMatchSource
        from riftlab.ingestion.infrastructure.riot_transport import (
            RiotFetchBudget,
            RiotHttpTransport,
        )

        engine = build_engine(settings.database_url)
        session_factory = build_session_factory(engine)
        client = None
        try:
            source: MatchSource
            if mode == "demo":
                source = DemoMatchSource()
            else:
                import httpx

                client = httpx.AsyncClient(follow_redirects=False)
                transport = RiotHttpTransport(
                    api_key=settings.riot_api_key or "",
                    budget=RiotFetchBudget(
                        max_matches=1,
                        max_requests=10,
                        max_attempts_per_request=1,
                        max_total_seconds=30.0,
                    ),
                    client=client,
                )
                raw_store = DurableRawMatchStore(Path(settings.raw_storage_dir))
                source = RiotMatchSource(transport, raw_store, settings.subject_hash_secret)

            result = await reconstruct_match(session_factory, mapping, source, match_id)
            typer.echo(
                f"matchId={result.match_id} catalogId={result.catalog_id} "
                f"decisionStates={len(result.decision_states)}"
            )
        finally:
            if client is not None:
                await client.aclose()
            await engine.dispose()

    try:
        asyncio.run(_run())
    except (SourceError, DomainError) as exc:
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
