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
import hashlib
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
import typer

if TYPE_CHECKING:
    from riftlab.collection.dataset_collection import Campaign

collect_app = typer.Typer(
    help="Coleta incremental de partidas por lista permitida de sementes (CC-206).",
    no_args_is_help=True,
)
report_app = typer.Typer(
    help="Relatórios agregados da amostra REAL, sem dado pessoal (CC-206).",
    no_args_is_help=True,
)
_ROOT = Path(__file__).resolve().parents[4]
_DEFAULT_CC316_SEEDS = _ROOT / ".private" / "collection" / "seeds.yaml"
_DEFAULT_CC316_OUTPUT = _ROOT / ".private" / "collection" / "cc316"
_DEFAULT_CC323_OUTPUT = _ROOT / ".private" / "collection" / "cc323"


def _effective_campaign_source_sha256() -> str:
    """Compatibility wrapper for campaign source fingerprinting."""
    from riftlab.collection.dataset_collection import effective_campaign_source_sha256

    return effective_campaign_source_sha256()


@collect_app.command("structural-cc316")
def collect_structural_cc316(
    seeds_file: Path = typer.Option(
        _DEFAULT_CC316_SEEDS, help="Lista privada de sementes com consentimento registrado."
    ),
    output_dir: Path = typer.Option(
        _DEFAULT_CC316_OUTPUT, help="Diretório privado dedicado a diário e partição estrutural."
    ),
    attempt: int = typer.Option(1, min=1, help="Rodada sequencial; repetir só após INTERRUPTED."),
    env_file: Path | None = typer.Option(None, help="Arquivo de ambiente Riot local."),
) -> None:
    """Executa uma rodada única de descoberta estrutural CC-316 (sem raw/timeline/DB)."""
    current_process_started_monotonic_ns = time.monotonic_ns()
    from riftlab.cli.env_file import load_settings
    from riftlab.collection.cc316_discovery import (
        DiscoveryError,
        ProcessLifecycleJournal,
        current_boot_id,
        process_lifecycle_path,
        require_candidate_budget_resolved,
        run_cc316_discovery,
        validate_prior_budget_history,
    )
    from riftlab.collection.seeds import SeedsFileError, load_seeds
    from riftlab.ingestion.infrastructure.riot_rate_limiter import RiotRateLimiter
    from riftlab.ingestion.infrastructure.riot_transport import (
        RiotFetchBudget,
        RiotHttpTransport,
    )

    try:
        require_candidate_budget_resolved()
    except DiscoveryError as exc:
        raise _blocked(str(exc)) from exc
    settings = load_settings(env_file)
    if not settings.riot_api_key:
        raise _blocked("chave Riot ausente; nada foi consultado")
    if not settings.subject_hash_secret:
        raise _blocked("segredo HMAC ausente; nada foi consultado")
    try:
        seeds = load_seeds(seeds_file)
    except (SeedsFileError, OSError) as exc:
        raise _blocked(f"sementes consentidas indisponíveis ({type(exc).__name__})") from exc

    if output_dir.name != "cc316" or ".private" not in output_dir.parts:
        raise _blocked("saída deve ser o subdiretório cc316 sob .private")
    try:
        output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        output_dir.chmod(0o700)
        if output_dir.stat().st_mode & 0o077:
            raise OSError("permissões privadas insuficientes")
    except OSError as exc:
        raise _blocked("diretório de saída privada indisponível") from exc

    artifact_path = output_dir / (
        "partition-snapshot.json"
        if attempt == 1
        else f"partition-snapshot-attempt-{attempt:02d}.json"
    )
    budget_path = output_dir / (
        "request-budget.json" if attempt == 1 else f"request-budget-{attempt:02d}.json"
    )
    lifecycle_path = process_lifecycle_path(budget_path, attempt)
    try:
        validate_prior_budget_history(
            budget_path,
            attempt,
            boot_identifier=current_boot_id(),
            current_process_started_monotonic_ns=current_process_started_monotonic_ns,
        )
    except DiscoveryError as exc:
        raise _blocked(str(exc)) from exc
    run_marker = output_dir / (
        ".cc316-discovery-consumed" if attempt == 1 else f".cc316-discovery-consumed-{attempt:02d}"
    )
    if (
        artifact_path.exists()
        or budget_path.exists()
        or lifecycle_path.exists()
        or run_marker.exists()
    ):
        raise _blocked(
            "esta tentativa já foi iniciada ou produziu estado local; revisão necessária"
        )

    try:
        lifecycle = ProcessLifecycleJournal(
            lifecycle_path,
            attempt_number=attempt,
            process_started_monotonic_ns=current_process_started_monotonic_ns,
        )
        lifecycle.__enter__()
    except DiscoveryError as exc:
        raise _blocked(str(exc)) from exc

    async def _run() -> Any:
        lifecycle.mark_coroutine_started()
        transport: RiotHttpTransport | None = None
        try:
            limiter = RiotRateLimiter()
            transport = RiotHttpTransport(
                api_key=settings.riot_api_key or "",
                budget=RiotFetchBudget(
                    max_matches=320,
                    max_requests=1_000,
                    max_attempts_per_request=5,
                    max_total_seconds=3_600,
                    request_timeout_seconds=10,
                ),
                rate_limiter=limiter,
            )
            return await run_cc316_discovery(
                transport=transport,
                seeds=seeds,
                subject_hash_secret=settings.subject_hash_secret or "",
                artifact_path=artifact_path,
                budget_journal_path=budget_path,
                attempt_number=attempt,
                process_started_monotonic_ns=lifecycle.process_started_monotonic_ns,
                boot_identifier=lifecycle.boot_id,
            )
        finally:
            try:
                if transport is not None:
                    await transport.aclose()
            finally:
                lifecycle.mark_coroutine_finished()

    result: Any | None = None
    run_error: Exception | None = None
    run_outcome = "INTERRUPTED"
    try:
        result = asyncio.run(_run())
        run_outcome = "COMPLETED"
    except Exception as exc:
        run_error = exc
    finally:
        lifecycle.finish(run_outcome=run_outcome)

    if run_error is not None:
        typer.echo(
            f"CC-316 interrompido: {type(run_error).__name__}; consulte o diário privado.",
            err=True,
        )
        raise typer.Exit(code=EXIT_INCOMPLETE) from run_error

    if result is None:
        raise _blocked("resultado da coleta ausente após o ciclo de processo")

    typer.echo(
        json.dumps(
            {
                field: getattr(result.summary, field)
                for field in result.summary.__dataclass_fields__
            },
            sort_keys=True,
            ensure_ascii=False,
        )
    )


@collect_app.command("cc316-adopt-partition")
def collect_cc316_adopt_partition(
    output_dir: Path = typer.Option(
        _DEFAULT_CC316_OUTPUT, help="Diretório privado cc316 com o snapshot da rodada 4."
    ),
) -> None:
    """Gera uma vez o artefato da partição adotada pela emenda CC-316-B (local, sem rede)."""
    import riftlab.collection.cc316_adoption as adoption
    from riftlab.collection.cc316_discovery import DiscoveryError

    if output_dir.name != "cc316" or ".private" not in output_dir.parts:
        raise _blocked("saída deve ser o subdiretório cc316 sob .private")
    try:
        adoption.verify_amendment(
            adoption.private_root_for(output_dir),
            expected_sha256=adoption.AMENDMENT_SHA256,
        )
        artifact = adoption.derive_adopted_partition(
            (output_dir / adoption.SOURCE_SNAPSHOT_FILENAME).read_bytes(),
            source_sha256=adoption.SOURCE_SNAPSHOT_SHA256,
            amendment_sha256=adoption.AMENDMENT_SHA256,
        )
        digest = adoption.persist_adopted_partition_once(
            output_dir / adoption.ADOPTED_PARTITION_FILENAME, artifact
        )
    except (DiscoveryError, OSError) as exc:
        raise _blocked(f"partição adotada não gerada ({exc})") from exc
    universe = artifact["universe"]
    partition = artifact["partition"]
    assert isinstance(universe, dict) and isinstance(partition, dict)
    typer.echo(
        json.dumps(
            {
                "adoptedPartitionSha256": digest,
                "candidateCount": universe["candidateCount"],
                "eligibleCount": universe["eligibleCount"],
                "explorationCount": len(partition["explorationMatchIds"]),
                "confirmationPotentialCount": len(partition["confirmationPotentialMatchIds"]),
                "overlapExcludedCount": len(partition["overlapExcludedMatchIds"]),
                "outOfScopeCount": len(partition["outOfScopeMatchIds"]),
            },
            sort_keys=True,
        )
    )


@collect_app.command("cc316-exploration-fetch")
def collect_cc316_exploration_fetch(
    attempt: int = typer.Option(..., min=7, help="Tentativa CC-316-B autorizada (7 ou posterior)."),
    authorization: Path = typer.Option(
        ..., help="Autorização privada da tentativa (JSON com sidecar SHA-256)."
    ),
    output_dir: Path = typer.Option(
        _DEFAULT_CC316_OUTPUT, help="Diretório privado cc316 da partição adotada."
    ),
    env_file: Path | None = typer.Option(None, help="Arquivo de ambiente Riot local."),
) -> None:
    """Busca match+timeline só das 20 partidas de exploração CC-316-B (sem banco/confirmação)."""
    _run_cc316_partition_fetch(
        "exploration", attempt, authorization, output_dir, env_file, time.monotonic_ns()
    )


@collect_app.command("cc316-confirmation-fetch")
def collect_cc316_confirmation_fetch(
    attempt: int = typer.Option(..., min=7, help="Tentativa CC-316-B autorizada (7 ou posterior)."),
    authorization: Path = typer.Option(
        ..., help="Autorização privada da tentativa (JSON com sidecar SHA-256)."
    ),
    output_dir: Path = typer.Option(
        _DEFAULT_CC316_OUTPUT, help="Diretório privado cc316 da partição adotada."
    ),
    env_file: Path | None = typer.Option(None, help="Arquivo de ambiente Riot local."),
) -> None:
    """Busca match+timeline só da confirmação potencial CC-316 depois do congelamento selado."""
    _run_cc316_partition_fetch(
        "confirmation", attempt, authorization, output_dir, env_file, time.monotonic_ns()
    )


def _run_cc316_partition_fetch(
    partition: str,
    attempt: int,
    authorization: Path,
    output_dir: Path,
    env_file: Path | None,
    current_process_started_monotonic_ns: int,
) -> None:
    import riftlab.collection.cc316_adoption as adoption
    import riftlab.collection.cc316_exploration as exploration
    from riftlab.cli.env_file import load_settings
    from riftlab.collection.cc316_discovery import (
        DiscoveryError,
        ProcessLifecycleJournal,
        current_boot_id,
        validate_prior_budget_history,
    )
    from riftlab.ingestion.infrastructure.riot_rate_limiter import RiotRateLimiter
    from riftlab.ingestion.infrastructure.riot_transport import (
        RiotFetchBudget,
        RiotHttpTransport,
    )

    adopted_sha256 = adoption.ADOPTED_PARTITION_SHA256
    try:
        max_requests = exploration.load_exploration_fetch_authorization(
            authorization,
            attempt_number=attempt,
            adopted_partition_sha256=adopted_sha256,
            amendment_sha256=adoption.AMENDMENT_SHA256,
            scope=exploration.FETCH_SCOPES[partition],
        )
    except DiscoveryError as exc:
        raise _blocked(str(exc)) from exc
    if output_dir.name != "cc316" or ".private" not in output_dir.parts:
        raise _blocked("saída deve ser o subdiretório cc316 sob .private")
    paths = exploration.exploration_attempt_paths(output_dir, attempt, partition)
    try:
        adoption.verify_amendment(
            adoption.private_root_for(output_dir),
            expected_sha256=adoption.AMENDMENT_SHA256,
        )
        if partition == "exploration":
            match_ids = adoption.load_adopted_exploration_ids(
                paths["adopted"], expected_sha256=adopted_sha256
            )
        else:
            match_ids = exploration.load_adopted_confirmation_ids(
                paths["adopted"],
                expected_sha256=adopted_sha256,
                freeze_sha256=exploration.FREEZE_SHA256,
            )
        validate_prior_budget_history(
            paths["budget"],
            attempt,
            boot_identifier=current_boot_id(),
            current_process_started_monotonic_ns=current_process_started_monotonic_ns,
        )
    except DiscoveryError as exc:
        raise _blocked(str(exc)) from exc
    if any(paths[name].exists() for name in ("report", "budget", "lifecycle", "marker")):
        raise _blocked(
            "esta tentativa já foi iniciada ou produziu estado local; revisão necessária"
        )
    settings = load_settings(env_file)
    if not settings.riot_api_key:
        raise _blocked("chave Riot ausente; nada foi consultado")
    try:
        exploration.connectivity_preflight()
    except DiscoveryError as exc:
        raise _blocked(str(exc)) from exc

    try:
        lifecycle = ProcessLifecycleJournal(
            paths["lifecycle"],
            attempt_number=attempt,
            process_started_monotonic_ns=current_process_started_monotonic_ns,
        )
        lifecycle.__enter__()
    except DiscoveryError as exc:
        raise _blocked(str(exc)) from exc

    async def _run() -> Any:
        lifecycle.mark_coroutine_started()
        transport: RiotHttpTransport | None = None
        try:
            transport = RiotHttpTransport(
                api_key=settings.riot_api_key or "",
                budget=RiotFetchBudget(
                    max_matches=len(match_ids),
                    max_requests=1_000,
                    max_attempts_per_request=5,
                    max_total_seconds=3_600,
                    request_timeout_seconds=10,
                ),
                rate_limiter=RiotRateLimiter(),
            )
            return await exploration.run_cc316_partition_fetch(
                partition=partition,
                transport=transport,
                output_dir=output_dir,
                attempt_number=attempt,
                authorized_max_requests=max_requests,
                adopted_partition_sha256=adopted_sha256,
                freeze_sha256=exploration.FREEZE_SHA256,
                process_started_monotonic_ns=lifecycle.process_started_monotonic_ns,
                boot_identifier=lifecycle.boot_id,
            )
        finally:
            try:
                if transport is not None:
                    await transport.aclose()
            finally:
                lifecycle.mark_coroutine_finished()

    result: Any | None = None
    run_error: Exception | None = None
    run_outcome = "INTERRUPTED"
    try:
        result = asyncio.run(_run())
        run_outcome = "COMPLETED"
    except Exception as exc:
        run_error = exc
    finally:
        lifecycle.finish(run_outcome=run_outcome)

    if run_error is not None:
        typer.echo(
            f"CC-316-B interrompido: {type(run_error).__name__}; consulte o diário privado.",
            err=True,
        )
        raise typer.Exit(code=EXIT_INCOMPLETE) from run_error
    if result is None:
        raise _blocked("resultado da busca ausente após o ciclo de processo")
    typer.echo(
        json.dumps(
            {field: getattr(result, field) for field in result.__dataclass_fields__},
            sort_keys=True,
            ensure_ascii=False,
        )
    )


@collect_app.command("dataset-16-19")
def collect_dataset_16_19(
    attempt: int = typer.Option(..., min=1, help="Tentativa autorizada da campanha CC-323."),
    authorization: Path = typer.Option(
        ..., help="Autorização privada da tentativa (JSON com sidecar SHA-256)."
    ),
    seeds_file: Path = typer.Option(
        _DEFAULT_CC316_SEEDS, help="Lista privada de sementes com consentimento registrado."
    ),
    output_dir: Path = typer.Option(
        _DEFAULT_CC323_OUTPUT, help="Diretório privado cc323 da coleta."
    ),
    env_file: Path | None = typer.Option(None, help="Arquivo de ambiente Riot local."),
) -> None:
    """Coleta CC-323: pares 16.19 por expansão a partir das sementes consentidas (sem banco)."""
    import riftlab.collection.dataset_collection as dataset

    _run_dataset_campaign(dataset.CC323, attempt, authorization, seeds_file, output_dir, env_file)


@collect_app.command("dataset-16-19-scale")
def collect_dataset_16_19_scale(
    attempt: int = typer.Option(..., min=1, help="Tentativa autorizada da campanha CC-703."),
    authorization: Path = typer.Option(
        ..., help="Autorização privada da tentativa (JSON com sidecar SHA-256)."
    ),
    seeds_file: Path = typer.Option(
        _DEFAULT_CC316_SEEDS, help="Lista privada de sementes com consentimento registrado."
    ),
    output_dir: Path = typer.Option(
        _DEFAULT_CC323_OUTPUT.parent / "cc703", help="Diretório privado cc703 da coleta."
    ),
    env_file: Path | None = typer.Option(None, help="Arquivo de ambiente Riot local."),
) -> None:
    """Coleta CC-703: até 10 mil pares 16.19, dez atiradores, 5 partidas sorteadas por jogador."""
    import riftlab.collection.dataset_collection as dataset

    _run_dataset_campaign(dataset.CC703, attempt, authorization, seeds_file, output_dir, env_file)


def _run_dataset_campaign(
    campaign: Campaign,
    attempt: int,
    authorization: Path,
    seeds_file: Path,
    output_dir: Path,
    env_file: Path | None,
    *,
    campaign_manifest: dict[str, object] | None = None,
    campaign_manifest_sha256: str | None = None,
) -> None:
    current_process_started_monotonic_ns = time.monotonic_ns()
    import riftlab.collection.dataset_collection as dataset
    from riftlab.cli.env_file import load_settings
    from riftlab.collection.cc316_discovery import DiscoveryError, ProcessLifecycleJournal
    from riftlab.collection.cc316_exploration import connectivity_preflight
    from riftlab.collection.seeds import SeedsFileError, load_seeds
    from riftlab.ingestion.infrastructure.riot_rate_limiter import RiotRateLimiter
    from riftlab.ingestion.infrastructure.riot_transport import (
        RiotFetchBudget,
        RiotHttpTransport,
    )

    name = campaign.output_dir_name
    try:
        granted = dataset.load_collection_authorization(
            authorization,
            attempt_number=attempt,
            campaign=campaign,
            manifest_sha256=campaign_manifest_sha256,
        )
    except DiscoveryError as exc:
        raise _blocked(str(exc)) from exc
    if output_dir.name != name or ".private" not in output_dir.parts:
        raise _blocked(f"saída deve ser o subdiretório {name} sob .private")
    try:
        output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        output_dir.chmod(0o700)
    except OSError as exc:
        raise _blocked("diretório de saída privada indisponível") from exc
    if campaign.is_generic:
        if campaign_manifest is None or campaign_manifest_sha256 is None:
            raise _blocked("manifesto imutável v2 ausente; nada foi consultado")
        try:
            written_digest = dataset.persist_patch_campaign_manifest_once(
                output_dir, campaign_manifest
            )
        except DiscoveryError as exc:
            raise _blocked(str(exc)) from exc
        if written_digest != campaign_manifest_sha256:
            raise _blocked("hash do manifesto v2 divergente; nada foi consultado")
    paths = dataset.attempt_paths(output_dir, attempt)
    try:
        if campaign.is_generic:
            dataset.prior_patch_campaign_usage(output_dir, attempt, campaign_manifest_sha256 or "")
        else:
            dataset.prior_campaign_usage(output_dir, attempt)
    except DiscoveryError as exc:
        raise _blocked(str(exc)) from exc
    if any(paths[name].exists() for name in ("report", "budget", "lifecycle", "marker")):
        raise _blocked(
            "esta tentativa já foi iniciada ou produziu estado local; revisão necessária"
        )
    if campaign.is_generic:
        try:
            dataset.validate_private_campaign_path(
                _ROOT / ".private", paths["raw"], "raiz raw da campanha"
            )
        except DiscoveryError as exc:
            raise _blocked(str(exc)) from exc
    try:
        if hashlib.sha256(seeds_file.read_bytes()).hexdigest() != granted.seeds_sha256:
            raise _blocked("arquivo de sementes diverge do autorizado; nada foi consultado")
        seeds = load_seeds(seeds_file)
    except (SeedsFileError, OSError) as exc:
        raise _blocked(f"sementes consentidas indisponíveis ({type(exc).__name__})") from exc
    root = _ROOT / ".private" if campaign.is_generic else output_dir.parent
    known_raw_directories = [
        directory
        for directory in (root / relative for relative in campaign.known_raw_subdirectories)
        if directory.is_dir()
    ]
    if campaign.is_generic:
        input_hashes = campaign_manifest.get("inputHashes") if campaign_manifest else None
        if (
            not isinstance(input_hashes, dict)
            or input_hashes.get("toolSha256") != _effective_campaign_source_sha256()
        ):
            raise _blocked("fontes ativas da campanha divergem do manifesto; nada foi consultado")

    settings = load_settings(env_file)
    if not settings.riot_api_key:
        raise _blocked("chave Riot ausente; nada foi consultado")
    if not campaign.is_generic:
        try:
            connectivity_preflight()
        except DiscoveryError as exc:
            raise _blocked(str(exc)) from exc

    try:
        lifecycle = ProcessLifecycleJournal(
            paths["lifecycle"],
            attempt_number=attempt,
            process_started_monotonic_ns=current_process_started_monotonic_ns,
            binding=(
                {
                    "campaign": campaign.campaign_id,
                    "attemptNumber": attempt,
                    "manifestSha256": campaign_manifest_sha256,
                }
                if campaign.is_generic
                else None
            ),
        )
        lifecycle.__enter__()
    except DiscoveryError as exc:
        raise _blocked(str(exc)) from exc

    async def _run() -> Any:
        lifecycle.mark_coroutine_started()
        transport: RiotHttpTransport | None = None
        try:
            transport = RiotHttpTransport(
                api_key=settings.riot_api_key or "",
                budget=RiotFetchBudget(
                    max_matches=campaign.max_pairs,
                    max_requests=campaign.max_requests,
                    max_attempts_per_request=5,
                    max_total_seconds=campaign.max_seconds,
                    request_timeout_seconds=10,
                    stop_on_429=campaign.is_generic,
                ),
                rate_limiter=RiotRateLimiter(),
            )
            return await dataset.run_dataset_collection(
                transport=transport,
                seeds=seeds,
                output_dir=output_dir,
                attempt_number=attempt,
                authorized_max_requests=granted.max_requests,
                known_raw_directories=known_raw_directories,
                campaign=campaign,
                campaign_manifest_sha256=campaign_manifest_sha256,
                process_started_monotonic_ns=lifecycle.process_started_monotonic_ns,
            )
        finally:
            try:
                if transport is not None:
                    await transport.aclose()
            finally:
                lifecycle.mark_coroutine_finished()

    result: Any | None = None
    run_error: Exception | None = None
    run_outcome = "INTERRUPTED"
    try:
        result = asyncio.run(_run())
        run_outcome = "COMPLETED"
    except Exception as exc:
        run_error = exc
    finally:
        lifecycle.finish(run_outcome=run_outcome)

    if run_error is not None:
        typer.echo(
            f"{campaign.campaign_id} interrompido: {type(run_error).__name__}; "
            "consulte o diário privado.",
            err=True,
        )
        raise typer.Exit(code=EXIT_INCOMPLETE) from run_error
    if result is None:
        raise _blocked("resultado da coleta ausente após o ciclo de processo")
    typer.echo(
        json.dumps(
            {field: getattr(result, field) for field in result.__dataclass_fields__},
            sort_keys=True,
            ensure_ascii=False,
        )
    )
    if result.status not in dataset.DESIGNED_STOPS:
        raise typer.Exit(code=EXIT_INCOMPLETE)


@collect_app.command("patch-campaign")
def collect_patch_campaign(
    patch: str = typer.Option(..., help="Patch major.minor, numérico e explícito."),
    campaign_name: str = typer.Option(..., "--campaign", help="Nome ASCII seguro da campanha."),
    attempt: int = typer.Option(..., min=1, help="Tentativa sequencial desta campanha."),
    authorization: Path = typer.Option(..., help="Autorização v2 privada com sidecar SHA-256."),
    window_start: int = typer.Option(..., help="Início epoch da janela congelada."),
    window_end: int = typer.Option(..., help="Fim epoch exclusivo da janela congelada."),
    queues: str = typer.Option("420,440", help="Filas permitidas separadas por vírgula."),
    max_pairs: int = typer.Option(1_200, min=1, help="Teto global imutável de pares novos."),
    max_requests: int = typer.Option(3_000, min=1, help="Teto global imutável de requests."),
    max_seconds: int = typer.Option(7_200, min=1, help="Teto global imutável em segundos."),
    seeds_file: Path = typer.Option(_DEFAULT_CC316_SEEDS, help="Sementes consentidas privadas."),
    scope_file: Path | None = typer.Option(None, help="Recorte versionado da campanha."),
    known_raw_root: list[Path] | None = typer.Option(
        None, "--known-raw-root", help="Raiz raw privada existente para deduplicação (repetível)."
    ),
    env_file: Path | None = typer.Option(None, help="Arquivo de ambiente Riot local."),
) -> None:
    """Campanha snowball genérica v2. Sem autorização exata não inicia nem consulta rede."""
    import yaml

    import riftlab.collection.dataset_collection as dataset
    from riftlab.collection.cc316_adoption import canonical_json
    from riftlab.collection.cc316_discovery import DiscoveryError

    scope_file = scope_file or (_ROOT / "configs" / "scope.yaml")

    try:
        queue_ids = tuple(int(value.strip()) for value in queues.split(",") if value.strip())
        scope_bytes = scope_file.read_bytes()
        scope_document = yaml.safe_load(scope_bytes)
        if not isinstance(scope_document, dict):
            raise ValueError("recorte inválido")
        configured_queues = scope_document.get("queues", [scope_document.get("queue")])
        champions = scope_document.get("championIds")
        role = scope_document.get("role")
        map_id = scope_document.get("map")
        if (
            not isinstance(configured_queues, list)
            or any(
                isinstance(value, bool) or not isinstance(value, int) for value in configured_queues
            )
            or not set(queue_ids) <= set(configured_queues)
            or not isinstance(champions, list)
            or any(isinstance(value, bool) or not isinstance(value, int) for value in champions)
            or isinstance(map_id, bool)
            or not isinstance(map_id, int)
            or role != "BOTTOM"
        ):
            raise ValueError("escopo de fila/role/champions incompatível")
        private_root = _ROOT / ".private"
        known_roots: tuple[str, ...] | None = None
        if known_raw_root:
            normalized_roots: list[str] = []
            for raw_root in known_raw_root:
                if any(part.is_symlink() for part in (raw_root, *raw_root.parents)):
                    raise DiscoveryError("raiz raw conhecida contém symlink")
                relative = raw_root.resolve().relative_to(private_root.resolve())
                if not raw_root.is_dir():
                    raise DiscoveryError("raiz raw conhecida deve ser diretório existente")
                normalized_roots.append(relative.as_posix())
            if len(set(normalized_roots)) != len(normalized_roots):
                raise DiscoveryError("raiz raw conhecida duplicada")
            known_roots = tuple(normalized_roots)
        campaign = dataset.build_patch_campaign(
            patch=patch,
            campaign_name=campaign_name,
            window_start=window_start,
            window_end=window_end,
            max_pairs=max_pairs,
            max_requests=max_requests,
            max_seconds=max_seconds,
            queues=queue_ids,
            map_id=map_id,
            scope_champions=frozenset(champions),
            scope_position=role,
            **({"known_raw_subdirectories": known_roots} if known_roots is not None else {}),
        )
        if any(part.is_symlink() for part in (seeds_file, *seeds_file.parents)):
            raise DiscoveryError("arquivo de sementes contém symlink")
        try:
            seeds_file.resolve().relative_to(private_root.resolve())
        except ValueError as exc:
            raise DiscoveryError("arquivo de sementes deve ficar dentro de .private") from exc
        seeds_bytes = seeds_file.read_bytes()
        seeds_digest = hashlib.sha256(seeds_bytes).hexdigest()
        manifest = dataset.patch_campaign_manifest(
            campaign,
            private_root=private_root,
            output_dir=_ROOT / ".private" / "collection" / campaign.output_dir_name,
            seeds_sha256=seeds_digest,
            scope_sha256=hashlib.sha256(scope_bytes).hexdigest(),
            tool_sha256=_effective_campaign_source_sha256(),
        )
        manifest_digest = hashlib.sha256(canonical_json(manifest)).hexdigest()
        grant = dataset.load_collection_authorization(
            authorization,
            attempt_number=attempt,
            campaign=campaign,
            manifest_sha256=manifest_digest,
        )
        if grant.seeds_sha256 != seeds_digest:
            raise DiscoveryError("sementes divergem da autorização")
    except (DiscoveryError, OSError, UnicodeError, ValueError, TypeError, yaml.YAMLError) as exc:
        raise _blocked(
            f"campanha recusada ({type(exc).__name__}); nenhuma requisição iniciada"
        ) from exc

    _run_dataset_campaign(
        campaign,
        attempt,
        authorization,
        seeds_file,
        _ROOT / ".private" / "collection" / campaign.output_dir_name,
        env_file,
        campaign_manifest=manifest,
        campaign_manifest_sha256=manifest_digest,
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


@report_app.command("blockers")
def report_blockers(
    patch: str | None = typer.Option(None, help="Restringe ao patch numérico, por exemplo 16.20."),
    queue: int | None = typer.Option(None, help="Restringe à fila 420 ou 440."),
    in_scope: bool = typer.Option(
        False, "--in-scope", help="Restringe aos pontos dentro do recorte."
    ),
    env_file: Path | None = typer.Option(None, help="Caminho do .env do banco alvo."),
    scope: Path | None = typer.Option(None, help="Recorte real (padrão: configs/scope.yaml)."),
    as_json: bool = typer.Option(False, "--json", help="Emite JSON em vez de Markdown."),
) -> None:
    """Censo agregado da extração corrente; só SELECT em transações READ ONLY."""
    import re

    from riftlab.reports.blockers import build_blockers_report, render_blockers_report

    if patch is not None and not re.fullmatch(r"[0-9]{1,3}\.[0-9]{1,3}", patch):
        raise _blocked("--patch deve ser numérico (major.minor)")
    if queue is not None and queue not in {420, 440}:
        raise _blocked("--queue aceita somente 420 ou 440")

    async def build(session_factory: Any, loaded: Any) -> dict[str, Any]:
        return await build_blockers_report(
            session_factory,
            _scope_file(loaded, scope),
            patch=patch,
            queue=queue,
            in_scope_only=in_scope,
        )

    data = _run_db_report(env_file, build)
    _emit(data, render_blockers_report(data), as_json)


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
                    real_scope.queues, real_scope.map_id, selected, real_scope.version
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
            enqueue = await enqueue_collected_imports(
                session_factory, raw_store, fetched, secret=settings.subject_hash_secret
            )
            result: dict[str, Any] = {
                "collected": len(fetched),
                "enqueued": enqueue.enqueued,
                "alreadyRequested": enqueue.already_requested,
                "rawIncomplete": enqueue.raw_incomplete,
                "purged": enqueue.purged,
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
        f"{data['alreadyRequested']} | par bruto incompleto: {data['rawIncomplete']} | "
        f"expurgadas (ignoradas): {data['purged']}"
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
