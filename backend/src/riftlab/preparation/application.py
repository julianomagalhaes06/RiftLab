"""Preparação durável de uma partida importada (CC-205): garantir o catálogo,
reconstruir (v6) e extrair as decisões, para o fluxo "sem console".

Reaproveita a maquinaria de jobs do CC-201 (`import_jobs` com `kind=PREPARE`):
posse por lease com token de geração, heartbeat, backoff com teto, contagem de
tentativas e retomada depois de crash (lease vencido → outro worker reclama).
Estados públicos: PENDING (QUEUED ou aguardando nova tentativa), RUNNING,
READY (SUCCEEDED) e FAILED (com código seguro).

Fronteiras:
- NUNCA coleta a partida de novo: a reconstrução só relê a fixture demo ou o raw
  local já importado (`LocalRawMatchSource`, estruturalmente incapaz de rede).
- Rede só para o catálogo público do Data Dragon, e só quando o patch tem
  entrada explícita em `configs/catalog-mappings.yaml` e ainda não há catálogo
  canônico. Sem entrada no mapping não há rede: a partida é preparada sem
  catálogo e os pontos saem inelegíveis com MISSING_CATALOG.
- Idempotente pelas marcas existentes (reconstrução e extração): repetir a
  preparação não duplica nada.
- Erro estrutural → FAILED permanente com código seguro; erro transitório →
  RETRY_WAIT com backoff (e FAILED `RETRY_LIMIT_EXCEEDED` depois do limite)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Callable, Iterable, Mapping
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from riftlab.catalog.application import resolve_catalog_for_match, sync_catalog
from riftlab.catalog.domain import CatalogSource
from riftlab.catalog.infrastructure.catalog_mapping import CatalogMapping, load_catalog_mapping
from riftlab.catalog.infrastructure.curation import CURATION_RULES_VERSION
from riftlab.catalog.infrastructure.data_dragon_source import DataDragonCatalogSource
from riftlab.catalog.infrastructure.data_dragon_transport import (
    DataDragonTransport,
    RawDataDragonResponse,
)
from riftlab.catalog.infrastructure.demo_source import DemoCatalogSource, demo_catalog_mapping
from riftlab.catalog.patches import ensure_patch_mapping
from riftlab.champions.application import ChampionFetcher, ensure_champion_catalog
from riftlab.config import Settings
from riftlab.decisions.application import extract_decisions, resolve_current_versions
from riftlab.decisions.domain import DecisionError
from riftlab.ingestion.application import _renew_lease_periodically, _transient_retry_delay
from riftlab.ingestion.domain import ImportJob, ImportJobState, ImportMode, JobKind
from riftlab.ingestion.infrastructure.demo_source import DemoMatchSource
from riftlab.ingestion.infrastructure.raw_store import DurableRawMatchStore
from riftlab.ingestion.infrastructure.riot_source import LocalRawMatchSource
from riftlab.ingestion.infrastructure.subject_hash import SubjectHashSecretMissingError
from riftlab.ingestion.repository import SqlAlchemyIngestionRepository
from riftlab.observability import safe_exception_description
from riftlab.shared.api.enums import PreparationStatus
from riftlab.shared.db import unit_of_work
from riftlab.shared.domain import SourceOrigin
from riftlab.shared.errors import (
    ConflictError,
    DomainError,
    NotFoundError,
    SourceAuthError,
    SourceError,
    SourceNotFoundError,
    SourceRateLimitedError,
    SourceSchemaError,
)
from riftlab.timeline.application import (
    RECONSTRUCTION_ALGORITHM_VERSION,
    FrameLike,
    reconstruct_match,
)
from riftlab.timeline.domain import TimelineReplayError
from riftlab.timeline.empirical_rules import load_empirical_rules_or_disabled
from riftlab.timeline.models import MatchFramesModel

SessionFactory = async_sessionmaker[AsyncSession]
CatalogSourceFactory = Callable[[CatalogMapping], AbstractAsyncContextManager[CatalogSource]]

logger = logging.getLogger("riftlab.worker")

CODE_MATCH_NOT_FOUND = "MATCH_NOT_FOUND"
CODE_MAPPING_MISSING = "CATALOG_MAPPING_FILE_MISSING"
CODE_CONFLICT = "PREPARATION_CONFLICT"
CODE_UNEXPECTED = "PREPARATION_UNEXPECTED_ERROR"


@dataclass(frozen=True, slots=True)
class PreparationInfo:
    """Visão pública da preparação de uma partida."""

    status: PreparationStatus
    error_code: str | None
    retry_at: datetime | None
    attempts: int


def preparation_info(job: ImportJob | None) -> PreparationInfo:
    """QUEUED e RETRY_WAIT aparecem como PENDING (RETRY_WAIT com `retry_at` e o
    código da última falha transitória); SUCCEEDED é READY."""
    if job is None:
        return PreparationInfo(PreparationStatus.NOT_REQUESTED, None, None, 0)
    match job.state:
        case ImportJobState.QUEUED:
            status = PreparationStatus.PENDING
        case ImportJobState.RETRY_WAIT:
            status = PreparationStatus.PENDING
        case ImportJobState.RUNNING:
            status = PreparationStatus.RUNNING
        case ImportJobState.SUCCEEDED:
            status = PreparationStatus.READY
        case ImportJobState.FAILED:
            status = PreparationStatus.FAILED
    keep_error = job.state in (ImportJobState.FAILED, ImportJobState.RETRY_WAIT)
    return PreparationInfo(
        status,
        job.error_code if keep_error else None,
        job.retry_at if job.state == ImportJobState.RETRY_WAIT else None,
        job.attempts,
    )


@asynccontextmanager
async def _default_catalog_source(mapping: CatalogMapping) -> AsyncIterator[CatalogSource]:
    transport = DataDragonTransport()
    try:
        yield DataDragonCatalogSource(transport, mapping)
    finally:
        await transport.aclose()


async def _ensure_catalog(
    session_factory: SessionFactory,
    settings: Settings,
    job: ImportJob,
    patch: str,
    catalog_source_factory: CatalogSourceFactory,
) -> CatalogMapping:
    """Garante o catálogo do patch e devolve o mapping que a reconstrução deve
    usar. DEMO: sync local da fixture, sem rede. REAL: só há rede se o patch
    tiver entrada explícita no mapping E o canônico não existe ou foi curado por
    uma regra que NÃO é a corrente.

    Achado da coleta real (CC-206, rodada 2a): um canônico sincronizado antes da
    curadoria `data_dragon_v5` (ex.: `LEGACY_UNKNOWN`, do CC-202) era tratado como
    suficiente; o classificador então rejeitava TODOS os itens
    (`CLASSIFICATION_UNSUPPORTED_CURATION`) e nenhum ponto podia ser elegível, em
    silêncio. Agora o canônico só serve se tem a curadoria corrente; senão sincroniza
    de novo (conteúdo novo = linha nova e canônica; a antiga fica preservada)."""
    if job.mode == ImportMode.DEMO:
        demo_mapping = demo_catalog_mapping()
        if demo_mapping.resolve(patch) is not None:
            await sync_catalog(session_factory, DemoCatalogSource(), patch)
        return demo_mapping

    mapping_path = Path(settings.catalog_mappings_path)
    if not mapping_path.is_file():
        raise SourceSchemaError(
            code=CODE_MAPPING_MISSING,
            detail="mapping de catálogo não encontrado no caminho configurado",
        )
    overrides = load_catalog_mapping(mapping_path)
    transport = DataDragonTransport()
    try:
        mapping = await ensure_patch_mapping(session_factory, overrides, patch, transport)
    finally:
        await transport.aclose()
    if mapping.resolve(patch) is None:
        return mapping  # sem entrada: nenhuma rede, catálogo ausente (MISSING_CATALOG)
    canonical = await resolve_catalog_for_match(session_factory, mapping, patch)
    if canonical is None or canonical.curation_rules_version != CURATION_RULES_VERSION:
        async with catalog_source_factory(mapping) as source:
            await sync_catalog(session_factory, source, patch)
    return mapping


async def _default_champion_fetch(version: str, locale: str) -> RawDataDragonResponse:
    transport = DataDragonTransport()
    try:
        return await transport.fetch_champion_catalog(version, locale)
    finally:
        await transport.aclose()


async def _ensure_champions(
    session_factory: SessionFactory,
    job: ImportJob,
    mapping: CatalogMapping,
    patch: str,
    champion_fetch: ChampionFetcher,
) -> None:
    """Nomes e retratos (CC-208) são acessórios: DEMO nunca busca (não há campeão
    real a atribuir); em REAL, patch sem mapping não usa rede; e qualquer falha
    (CDN fora do ar, documento inválido, versão divergente) só é registrada — a
    partida continua preparada e a interface mostra o rótulo numérico."""
    if job.mode == ImportMode.DEMO:
        return
    try:
        await ensure_champion_catalog(session_factory, mapping, patch, champion_fetch)
    except SourceError as exc:
        logger.warning(
            "catálogo de campeões indisponível; seguindo sem nomes/retratos",
            extra={"code": exc.code},
        )


async def _prepare(
    session_factory: SessionFactory,
    raw_store: DurableRawMatchStore,
    settings: Settings,
    job: ImportJob,
    catalog_source_factory: CatalogSourceFactory,
    champion_fetch: ChampionFetcher = _default_champion_fetch,
) -> None:
    match_id = job.requested_match_id
    async with unit_of_work(session_factory) as session:
        match = await SqlAlchemyIngestionRepository(session).get_match(match_id)
    if match is None:
        raise NotFoundError(f"match {match_id!r} não encontrado")

    mapping = await _ensure_catalog(
        session_factory, settings, job, match.patch, catalog_source_factory
    )
    source = (
        DemoMatchSource()
        if job.mode == ImportMode.DEMO
        else LocalRawMatchSource(raw_store, settings.subject_hash_secret)
    )
    rules = load_empirical_rules_or_disabled(
        Path(settings.empirical_rules_path), reconstruction_version=RECONSTRUCTION_ALGORITHM_VERSION
    )
    resolution = rules.resolve(match.patch, match.queue)
    if job.mode != ImportMode.DEMO and resolution.application == "INHERITED":
        try:
            await _ensure_catalog(
                session_factory,
                settings,
                job,
                resolution.source_patch or "",
                catalog_source_factory,
            )
        except SourceError:
            logger.warning("catálogo de origem indisponível; herança restrita")
    await reconstruct_match(session_factory, mapping, source, match_id, empirical_rules=rules)
    await extract_decisions(session_factory, match_id, scope_path=Path(settings.scope_path))
    await _ensure_champions(session_factory, job, mapping, match.patch, champion_fetch)
    await _ensure_frames(session_factory, source, match_id)
    if job.mode != ImportMode.DEMO:
        from riftlab.ingestion.final_items_backfill import backfill_final_items
        from riftlab.shared.errors import SourceSchemaError
        from riftlab.timeline.health import compute_health

        backfill = await backfill_final_items(
            session_factory, Path(settings.raw_storage_dir), match_id
        )
        if backfill.status == "RAW_INVALID":
            raise SourceSchemaError(
                code="FINAL_ITEMS_RAW_INVALID",
                detail="dados locais inválidos para preencher o inventário final",
            )
        await compute_health(session_factory, rules, match.patch)


def frames_document(frames: Iterable[FrameLike]) -> list[dict[str, Any]]:
    """Só o ouro total de cada participante por quadro; valor inválido fica de fora."""
    document: list[dict[str, Any]] = []
    for frame in frames:
        gold: dict[str, int] = {}
        for participant_id, data in frame.participant_frames.items():
            value = data.get("totalGold") if isinstance(data, Mapping) else None
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                gold[str(int(participant_id))] = value
        document.append({"t": frame.timestamp_ms, "gold": gold})
    return document


async def _ensure_frames(
    session_factory: SessionFactory, source: DemoMatchSource | LocalRawMatchSource, match_id: str
) -> None:
    """Grava o ouro por quadro uma única vez (só a preparação lê o bruto). É acessório:
    sem o bruto local (ex.: partida de corpus guardado fora do worker), a preparação
    termina normalmente e só o gráfico de ouro fica indisponível."""
    async with unit_of_work(session_factory) as session:
        if await session.get(MatchFramesModel, match_id) is not None:
            return
    try:
        result = await source.load_match(match_id)
    except SourceError as exc:
        logger.warning(
            "quadros de ouro indisponíveis; seguindo sem gráfico", extra={"code": exc.code}
        )
        return
    async with unit_of_work(session_factory) as session:
        if await session.get(MatchFramesModel, match_id) is None:
            session.add(MatchFramesModel(match_id=match_id, frames=frames_document(result.frames)))


async def _finalize(
    session_factory: SessionFactory,
    job: ImportJob,
    *,
    failed: str | None = None,
    retry: tuple[str, datetime] | None = None,
) -> None:
    async with unit_of_work(session_factory) as session:
        repo = SqlAlchemyIngestionRepository(session)
        if failed is not None:
            await repo.mark_import_failed(job.id, job.lease_generation, failed)
        elif retry is not None:
            await repo.mark_import_retry_wait(job.id, job.lease_generation, retry[1], retry[0])
        else:
            ok = await repo.mark_import_succeeded(
                job.id, job.lease_generation, job.requested_match_id
            )
            if not ok:
                logger.warning("worker perdeu a posse da preparação %s antes de concluir", job.id)


def _retry_at(job: ImportJob, retry_after_seconds: float | None = None) -> datetime:
    return datetime.now(UTC) + timedelta(
        seconds=_transient_retry_delay(job.attempts, retry_after_seconds)
    )


async def process_next_preparation(
    session_factory: SessionFactory,
    raw_store: DurableRawMatchStore,
    settings: Settings,
    catalog_source_factory: CatalogSourceFactory | None = None,
    champion_fetch: ChampionFetcher | None = None,
) -> bool:
    """Um ciclo do worker para PREPARE. True se uma preparação foi processada
    (mesmo que tenha falhado); False se não havia trabalho elegível.
    `catalog_source_factory` é injetável só para teste (fonte stub, sem rede
    real); o worker nunca passa isto."""
    async with unit_of_work(session_factory) as session:
        job = await SqlAlchemyIngestionRepository(session).reserve_next_job(
            datetime.now(UTC), JobKind.PREPARE
        )
    if job is None:
        return False

    heartbeat = asyncio.create_task(
        _renew_lease_periodically(session_factory, job.id, job.lease_generation)
    )
    try:
        await _prepare(
            session_factory,
            raw_store,
            settings,
            job,
            catalog_source_factory or _default_catalog_source,
            champion_fetch or _default_champion_fetch,
        )
    except SubjectHashSecretMissingError:
        await _finalize(session_factory, job, failed="SUBJECT_HASH_SECRET_MISSING")
    except SourceRateLimitedError as exc:
        await _finalize(
            session_factory, job, retry=(exc.code, _retry_at(job, exc.retry_after_seconds))
        )
    except (SourceAuthError, SourceNotFoundError, SourceSchemaError) as exc:
        await _finalize(session_factory, job, failed=exc.code)  # estrutural
    except SourceError as exc:  # transitório (rede, 5xx, indisponibilidade)
        await _finalize(session_factory, job, retry=(exc.code, _retry_at(job)))
    except (TimelineReplayError, DecisionError) as exc:  # estrutural
        await _finalize(session_factory, job, failed=exc.code)
    except NotFoundError:
        await _finalize(session_factory, job, failed=CODE_MATCH_NOT_FOUND)
    except ConflictError:  # ex.: extração/reconstrução concorrente: tenta de novo
        await _finalize(session_factory, job, retry=(CODE_CONFLICT, _retry_at(job)))
    except Exception as exc:
        logger.error(
            "preparação %s falhou de forma inesperada detalhe=%s",
            job.id,
            safe_exception_description(exc),
        )
        await _finalize(session_factory, job, retry=(CODE_UNEXPECTED, _retry_at(job)))
    else:
        await _finalize(session_factory, job)
    finally:
        heartbeat.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await heartbeat
    return True


async def request_preparation(
    session_factory: SessionFactory, match_id: str, *, scope_path: Path
) -> ImportJob:
    """POST /matches/{matchId}/preparation: idempotente. Preparação ativa →
    devolve-a; FAILED ou inexistente → nova tentativa (histórico preservado,
    ligada à anterior); READY → devolve-a, exceto se as decisões das versões
    CORRENTES já não existem (ex.: nova versão de reconstrução), caso em que
    enfileira uma nova. Nunca coleta a partida de novo."""
    async with unit_of_work(session_factory) as session:
        repo = SqlAlchemyIngestionRepository(session)
        if await repo.get_match(match_id) is None:
            raise NotFoundError(f"match {match_id!r} não encontrado")
        mode = await repo.get_match_import_mode(match_id)
        if mode is None:
            raise NotFoundError(f"match {match_id!r} sem importação concluída")
        latest = await repo.get_latest_preparation_job(match_id)
        force = False
        if latest is not None and latest.state == ImportJobState.SUCCEEDED:
            force = not await _decisions_are_current(session, match_id, scope_path) or (
                await session.get(MatchFramesModel, match_id) is None
            )
        return await repo.enqueue_preparation(match_id, mode, force=force)


async def _decisions_are_current(session: AsyncSession, match_id: str, scope_path: Path) -> bool:
    from riftlab.decisions.repository import SqlAlchemyDecisionRepository

    try:
        current = await resolve_current_versions(session, match_id, scope_path)
    except DomainError:
        return False
    extraction = await SqlAlchemyDecisionRepository(session).get_extraction(
        match_id, current.versions
    )
    return extraction is not None and (
        current.origin is SourceOrigin.CURATED_DEMO or current.catalog is not None
    )
