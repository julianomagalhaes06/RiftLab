"""CLI da coleta incremental e dos relatórios da amostra (CC-206).

    cc collect run     coleta (lista permitida de sementes, tetos, limitação global)
    cc collect report  relatório da coleta a partir do manifesto (sem rede, sem banco)
    cc collect import  enfileira a importação a partir do raw local e (opcional) drena
    cc report reconstruction | diversity | integrity   relatórios da amostra no banco

Segredos: a chave da Riot e o segredo do hash de jogador vêm SÓ do `.env`/ambiente,
nunca de argumento. Nada aqui imprime chave, Riot ID, PUUID, hash de jogador ou matchId.

Códigos de saída de `collect run`: 0 terminou ou parou num teto (retome quando quiser);
1 recusou antes de qualquer requisição (BLOCKED); 3 credencial (401/403: troque a chave e
use --resume-after-auth-failure); 4 limite de taxa (espere o cooldown); 5 parcial
(alguma semente foi pulada/travada ou o servidor está instável)."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import typer

collect_app = typer.Typer(
    help="Coleta incremental de partidas por lista permitida de sementes (CC-206).",
    no_args_is_help=True,
)
report_app = typer.Typer(
    help="Relatórios agregados da amostra REAL, sem dado pessoal (CC-206).",
    no_args_is_help=True,
)


@report_app.command("patch-readiness")
def report_patch_readiness(
    env_file: Path = typer.Option(None, help="Arquivo de ambiente do banco alvo."),
) -> None:
    """Triagem de amostra por patch, só leitura e sem identificadores pessoais."""
    from riftlab.cli.env_file import load_settings
    from riftlab.db import build_engine
    from riftlab.reports.patch_readiness import patch_readiness
    from riftlab.shared.db import build_session_factory

    async def run() -> dict[str, object]:
        engine = build_engine(load_settings(env_file).database_url)
        try:
            settings = load_settings(env_file)
            return await patch_readiness(build_session_factory(engine), _scope_file(settings, None))
        finally:
            await engine.dispose()

    typer.echo(json.dumps(asyncio.run(run()), indent=2, sort_keys=True))


@report_app.command("gate")
def report_gate(
    frozen_dir: Path = typer.Option(..., help="Diretório dos agregados congelados do CC-207."),
    catalog_dir: Path = typer.Option(..., help="Diretório local dos item.json 16.16.1/16.17.1."),
    rules_dir: Path = typer.Option(..., help="Diretório dos manifests versionados de itens."),
    mapping_file: Path = typer.Option(..., help="Mapping explícito patch → Data Dragon."),
) -> None:
    """Diagnóstico offline: somente leitura de agregados e catálogos indicados.

    Não carrega .env nem abre banco, raw de partidas, Riot ou auditor congelado.
    """
    from riftlab.reports.gate_diagnostic import GateDiagnosticError, diagnose_frozen_gate

    try:
        data = diagnose_frozen_gate(frozen_dir, catalog_dir, rules_dir, mapping_file)
    except (GateDiagnosticError, OSError, ValueError, KeyError, TypeError) as exc:
        typer.echo(f"BLOCKED: diagnóstico incompatível ({type(exc).__name__}).", err=True)
        raise typer.Exit(code=EXIT_BLOCKED) from exc
    typer.echo(json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False))


# backend/src/riftlab/cli/collect.py -> parents[4] é a raiz do repositório.
DEFAULT_SCOPE_FILE = Path(__file__).resolve().parents[4] / "configs" / "scope.yaml"

EXIT_BLOCKED = 1
EXIT_AUTH = 3
EXIT_RATE_LIMIT = 4
EXIT_INCOMPLETE = 5
_EXIT_BY_REASON = {
    "AUTH_FAILED": EXIT_AUTH,
    "AUTH_BLOCKED": EXIT_AUTH,
    "RATE_LIMITED": EXIT_RATE_LIMIT,
    "COOLDOWN_ACTIVE": EXIT_RATE_LIMIT,
    "UPSTREAM_UNSTABLE": EXIT_INCOMPLETE,
    "PARTIAL": EXIT_INCOMPLETE,
    "INTERRUPTED": EXIT_INCOMPLETE,
}


def _http_client() -> httpx.AsyncClient | None:
    """Gancho de teste: em produção o transporte cria o próprio cliente."""
    return None


def _blocked(message: str) -> typer.Exit:
    typer.echo(f"BLOCKED: {message}", err=True)
    return typer.Exit(code=EXIT_BLOCKED)


def _raw_dir(settings: Any, explicit: Path | None) -> Path:
    resolved = explicit or Path(settings.raw_storage_dir)
    try:
        resolved.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise _blocked(
            f"diretório de raw {resolved} indisponível; informe --raw-dir (fora de container "
            "o padrão /app/data/raw/imports não existe)"
        ) from exc
    return resolved


def _scope_file(settings: Any, explicit: Path | None) -> Path:
    if explicit is not None:
        return explicit
    configured = Path(settings.scope_path)
    return configured if configured.is_file() else DEFAULT_SCOPE_FILE


def _epoch(day: str, *, option: str) -> int:
    try:
        parsed = datetime.fromisoformat(day)
    except ValueError as exc:
        raise _blocked(f"{option} deve ser uma data AAAA-MM-DD") from exc
    return int(parsed.replace(tzinfo=UTC).timestamp())


def _emit(data: dict[str, Any], text: str, as_json: bool) -> None:
    typer.echo(json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) if as_json else text)


# ---------------------------------------------------------------------------
# cc collect
# ---------------------------------------------------------------------------


@collect_app.command("run")
def collect_run(
    seeds_file: Path = typer.Option(
        ..., help="Lista permitida de sementes (YAML): só contas com consentimento explícito."
    ),
    env_file: Path = typer.Option(None, help="Caminho do .env (padrão: raiz do repositório)."),
    raw_dir: Path = typer.Option(
        None,
        help="Armazenamento local (o MESMO que o worker lê; padrão: RIFTLAB_RAW_STORAGE_DIR). "
        "O manifesto fica em <raw-dir>/collection/.",
    ),
    scope: Path = typer.Option(None, help="Recorte (padrão: configs/scope.yaml)."),
    patches: str = typer.Option(
        None,
        help="Patches separados por vírgula; só pode ESTREITAR o recorte, nunca alargá-lo.",
    ),
    max_requests: int = typer.Option(60, help="Teto de requisições desta execução."),
    max_matches: int = typer.Option(10, help="Teto de partidas NOVAS gravadas nesta execução."),
    max_duration_seconds: float = typer.Option(600.0, help="Teto de duração desta execução (s)."),
    max_matches_per_seed: int = typer.Option(
        None, help="Teto de partidas novas POR semente (equilibra a amostra)."
    ),
    page_size: int = typer.Option(100, help="Tamanho da página da listagem (1..100)."),
    since: str = typer.Option(None, help="Só partidas a partir desta data (AAAA-MM-DD, UTC)."),
    until: str = typer.Option(None, help="Só partidas até esta data (AAAA-MM-DD, UTC)."),
    resume_after_auth_failure: bool = typer.Option(
        False,
        "--resume-after-auth-failure",
        help="Confirma que a chave foi TROCADA depois de um 401/403; sem isto a coleta "
        "bloqueada recusa antes de qualquer requisição.",
    ),
    as_json: bool = typer.Option(False, "--json", help="Saída em JSON."),
) -> None:
    """Uma execução da coleta. Recusa (BLOCKED, nada é buscado) sem a chave da Riot, sem o
    segredo do hash de jogador, com sementes inválidas ou com os tetos inválidos."""
    from riftlab.cli.env_file import load_settings
    from riftlab.collection.collector import (
        CollectionCeilings,
        build_collection_transport,
        run_collection,
    )
    from riftlab.collection.filters import MatchFilterConfig
    from riftlab.collection.manifest import CollectionManifest, ManifestError, ManifestLockedError
    from riftlab.collection.report import build_collection_report, render_collection_report
    from riftlab.collection.seeds import SeedsFileError, load_seeds
    from riftlab.decisions.domain import DecisionConfigError
    from riftlab.decisions.scope import load_real_scope
    from riftlab.ingestion.infrastructure.raw_store import DurableRawMatchStore
    from riftlab.ingestion.infrastructure.riot_rate_limiter import RiotRateLimiter
    from riftlab.ingestion.infrastructure.subject_hash import SubjectHashSecretMissingError

    settings = load_settings(env_file)
    if settings.riot_api_key is None:
        raise _blocked("RIFTLAB_RIOT_API_KEY não configurada (.env/ambiente). Nada foi buscado.")
    if not settings.subject_hash_secret:
        raise _blocked(
            "RIFTLAB_SUBJECT_HASH_SECRET não configurado (.env/ambiente); a coleta real valida "
            "cada par com o parser do importador e nunca gera esse segredo. Nada foi buscado."
        )
    try:
        seeds = load_seeds(seeds_file)

        async def read_window() -> tuple[str, ...] | None:
            from riftlab.catalog.patches import current_patch_window
            from riftlab.db import build_engine
            from riftlab.shared.db import build_session_factory, read_only_unit_of_work

            engine = build_engine(settings.database_url)
            try:
                async with read_only_unit_of_work(build_session_factory(engine)) as session:
                    return await current_patch_window(session)
            finally:
                await engine.dispose()

        scope_path = _scope_file(settings, scope)
        import yaml

        scope_document = yaml.safe_load(scope_path.read_bytes())
        window = (
            asyncio.run(read_window())
            if isinstance(scope_document, dict) and "patchWindow" in scope_document
            else None
        )
        real_scope = load_real_scope(scope_path, window)
        ceilings = CollectionCeilings(
            max_requests=max_requests,
            max_matches=max_matches,
            max_duration_seconds=max_duration_seconds,
            max_matches_per_seed=max_matches_per_seed,
            page_size=page_size,
        )
    except (SeedsFileError, DecisionConfigError, ValueError, OSError) as exc:
        raise _blocked(str(exc)) from exc

    selected = real_scope.patches
    if patches:
        wanted = frozenset(part.strip() for part in patches.split(",") if part.strip())
        if not wanted <= real_scope.patches:
            raise _blocked("--patches só pode estreitar o recorte de configs/scope.yaml")
        selected = wanted
    start_time = _epoch(since, option="--since") if since else None
    end_time = _epoch(until, option="--until") if until else None
    if start_time is not None and end_time is not None and start_time >= end_time:
        raise _blocked("--since deve ser anterior a --until")

    resolved_raw = _raw_dir(settings, raw_dir)
    manifest_dir = resolved_raw / "collection"

    async def _run() -> Any:
        limiter = RiotRateLimiter()
        client = _http_client()
        transport = build_collection_transport(
            settings.riot_api_key or "", ceilings, client=client, rate_limiter=limiter
        )
        try:
            return await run_collection(
                transport=transport,
                raw_store=DurableRawMatchStore(resolved_raw),
                manifest=CollectionManifest(manifest_dir),
                seeds=seeds,
                filters=MatchFilterConfig(
                    real_scope.queue, real_scope.map_id, selected, real_scope.version
                ),
                subject_hash_secret=settings.subject_hash_secret,
                ceilings=ceilings,
                rate_limiter=limiter,
                resume_after_auth_failure=resume_after_auth_failure,
                start_time=start_time,
                end_time=end_time,
            )
        finally:
            await transport.aclose()
            if client is not None:
                await client.aclose()

    try:
        report = asyncio.run(_run())
    except (ManifestLockedError, ManifestError, SubjectHashSecretMissingError) as exc:
        raise _blocked(str(exc)) from exc

    data = build_collection_report(CollectionManifest(manifest_dir))
    _emit(data, render_collection_report(data), as_json)
    raise typer.Exit(code=_EXIT_BY_REASON.get(str(report.stop_reason), 0))


@collect_app.command("report")
def collect_report(
    env_file: Path = typer.Option(None, help="Caminho do .env (padrão: raiz do repositório)."),
    raw_dir: Path = typer.Option(
        None, help="Armazenamento local (padrão: RIFTLAB_RAW_STORAGE_DIR)."
    ),
    run: int = typer.Option(None, help="N-ésima rodada (1 = primeira); padrão: a última."),
    as_json: bool = typer.Option(False, "--json", help="Saída em JSON."),
) -> None:
    """Relatório da coleta lido do manifesto: sem rede, sem banco, sem dado pessoal."""
    from riftlab.cli.env_file import load_settings
    from riftlab.collection.manifest import CollectionManifest, ManifestError
    from riftlab.collection.report import build_collection_report, render_collection_report

    settings = load_settings(env_file)
    manifest_dir = _raw_dir(settings, raw_dir) / "collection"
    if not (manifest_dir / "manifest.json").exists():
        raise _blocked(f"nenhum manifesto de coleta em {manifest_dir}")
    try:
        data = build_collection_report(CollectionManifest(manifest_dir), run=run)
    except (ManifestError, IndexError) as exc:
        raise _blocked(f"rodada ou manifesto inválido: {exc}") from exc
    _emit(data, render_collection_report(data), as_json)


@collect_app.command("import")
def collect_import(
    env_file: Path = typer.Option(None, help="Caminho do .env (padrão: raiz do repositório)."),
    raw_dir: Path = typer.Option(
        None, help="Armazenamento local (padrão: RIFTLAB_RAW_STORAGE_DIR)."
    ),
    drain: bool = typer.Option(
        True,
        "--drain/--no-drain",
        help="Drena a fila em processo (importação SÓ do raw local + preparação). Com "
        "--no-drain apenas enfileira e o worker faz o resto.",
    ),
    as_json: bool = typer.Option(False, "--json", help="Saída em JSON."),
) -> None:
    """Enfileira a importação das partidas COLETADAS a partir do raw local (nunca busca de
    novo) e, por padrão, drena a fila. Cada importação enfileira a preparação automática."""
    from riftlab.cli.env_file import load_settings
    from riftlab.collection.importing import drain_local, enqueue_collected_imports
    from riftlab.collection.manifest import CollectionManifest, ManifestError
    from riftlab.db import build_engine
    from riftlab.ingestion.infrastructure.raw_store import DurableRawMatchStore
    from riftlab.shared.db import build_session_factory

    settings = load_settings(env_file)
    resolved_raw = _raw_dir(settings, raw_dir)
    manifest_dir = resolved_raw / "collection"
    if not (manifest_dir / "manifest.json").exists():
        raise _blocked(f"nenhum manifesto de coleta em {manifest_dir}")
    try:
        fetched = CollectionManifest(manifest_dir).fetched_ids()
    except ManifestError as exc:
        raise _blocked(str(exc)) from exc
    raw_store = DurableRawMatchStore(resolved_raw)

    async def _run() -> dict[str, Any]:
        engine = build_engine(settings.database_url)
        session_factory = build_session_factory(engine)
        try:
            enqueue = await enqueue_collected_imports(session_factory, raw_store, fetched)
            result: dict[str, Any] = {
                "collected": len(fetched),
                "enqueued": enqueue.enqueued,
                "alreadyRequested": enqueue.already_requested,
                "rawIncomplete": enqueue.raw_incomplete,
                "drained": None,
            }
            if drain:
                drained = await drain_local(session_factory, raw_store, settings)
                result["drained"] = {
                    "importCycles": drained.import_cycles,
                    "preparationCycles": drained.preparation_cycles,
                }
            return result
        finally:
            await engine.dispose()

    data = asyncio.run(_run())
    text = (
        f"coletadas: {data['collected']} | enfileiradas: {data['enqueued']} | já pedidas: "
        f"{data['alreadyRequested']} | par bruto incompleto: {data['rawIncomplete']}"
    )
    if data["drained"] is not None:
        text += (
            f" | importações: {data['drained']['importCycles']} | preparações: "
            f"{data['drained']['preparationCycles']}"
        )
    _emit(data, text, as_json)


# ---------------------------------------------------------------------------
# cc report
# ---------------------------------------------------------------------------


def _sample_ids(settings: Any, raw_dir: Path | None, only_collected: bool) -> list[str] | None:
    if not only_collected:
        return None
    from riftlab.collection.manifest import CollectionManifest, ManifestError

    manifest_dir = _raw_dir(settings, raw_dir) / "collection"
    if not (manifest_dir / "manifest.json").exists():
        raise _blocked(f"nenhum manifesto de coleta em {manifest_dir}")
    try:
        return CollectionManifest(manifest_dir).fetched_ids()
    except ManifestError as exc:
        raise _blocked(str(exc)) from exc


def _run_db_report(env_file: Path | None, build: Any) -> Any:
    from riftlab.cli.env_file import load_settings
    from riftlab.db import build_engine
    from riftlab.shared.db import build_session_factory

    settings = load_settings(env_file)

    async def _run() -> Any:
        engine = build_engine(settings.database_url)
        try:
            return await build(build_session_factory(engine), settings)
        finally:
            await engine.dispose()

    return asyncio.run(_run())


@report_app.command("reconstruction")
def report_reconstruction(
    env_file: Path = typer.Option(None, help="Caminho do .env (padrão: raiz do repositório)."),
    scope: Path = typer.Option(None, help="Recorte (padrão: configs/scope.yaml)."),
    raw_dir: Path = typer.Option(None, help="Armazenamento local (com --only-collected)."),
    only_collected: bool = typer.Option(
        False, "--only-collected", help="Restringe às partidas do manifesto da coleta."
    ),
    as_json: bool = typer.Option(False, "--json", help="Saída em JSON."),
) -> None:
    """Reconstrução e decisões da amostra REAL: estados, pontos, elegíveis, motivos."""
    from riftlab.cli.env_file import load_settings
    from riftlab.reports.reconstruction import (
        build_reconstruction_report,
        render_reconstruction_report,
    )

    ids = _sample_ids(load_settings(env_file), raw_dir, only_collected)

    async def build(session_factory: Any, settings: Any) -> dict[str, Any]:
        report = await build_reconstruction_report(
            session_factory, _scope_file(settings, scope), match_ids=ids
        )
        return report.as_dict()

    data = _run_db_report(env_file, build)
    _emit(data, render_reconstruction_report(data), as_json)


@report_app.command("diversity")
def report_diversity(
    env_file: Path = typer.Option(None, help="Caminho do .env (padrão: raiz do repositório)."),
    scope: Path = typer.Option(None, help="Recorte (padrão: configs/scope.yaml)."),
    raw_dir: Path = typer.Option(None, help="Armazenamento local (com --only-collected)."),
    only_collected: bool = typer.Option(
        False, "--only-collected", help="Restringe às partidas do manifesto da coleta."
    ),
    as_json: bool = typer.Option(False, "--json", help="Saída em JSON."),
) -> None:
    """Diversidade/concentração por jogador pseudonimizado (só agregados) e cobertura."""
    from riftlab.cli.env_file import load_settings
    from riftlab.reports.diversity import build_diversity_report, render_diversity_report

    ids = _sample_ids(load_settings(env_file), raw_dir, only_collected)

    async def build(session_factory: Any, settings: Any) -> dict[str, Any]:
        return await build_diversity_report(
            session_factory, _scope_file(settings, scope), match_ids=ids
        )

    data = _run_db_report(env_file, build)
    _emit(data, render_diversity_report(data), as_json)


@report_app.command("integrity")
def report_integrity(
    env_file: Path = typer.Option(None, help="Caminho do .env (padrão: raiz do repositório)."),
    scope: Path = typer.Option(None, help="Recorte (padrão: configs/scope.yaml)."),
    raw_dir: Path = typer.Option(None, help="Armazenamento local (confere o par bruto)."),
    only_collected: bool = typer.Option(
        False, "--only-collected", help="Restringe às partidas do manifesto da coleta."
    ),
    as_json: bool = typer.Option(False, "--json", help="Saída em JSON."),
) -> None:
    """Verifica no banco que nenhuma partida parcial é analisável. Sai com 1 se houver violação."""
    from riftlab.cli.env_file import load_settings
    from riftlab.ingestion.infrastructure.raw_store import DurableRawMatchStore
    from riftlab.reports.integrity import check_integrity, render_integrity_report

    base = load_settings(env_file)
    ids = _sample_ids(base, raw_dir, only_collected)
    raw_store = DurableRawMatchStore(_raw_dir(base, raw_dir)) if raw_dir or only_collected else None

    async def build(session_factory: Any, settings: Any) -> dict[str, Any]:
        return await check_integrity(
            session_factory, _scope_file(settings, scope), raw_store=raw_store, match_ids=ids
        )

    data = _run_db_report(env_file, build)
    _emit(data, render_integrity_report(data), as_json)
    if not data["ok"]:
        raise typer.Exit(code=EXIT_BLOCKED)
