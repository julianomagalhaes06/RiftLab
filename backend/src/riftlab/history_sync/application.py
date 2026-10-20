"""CC-901: sincronizar o histórico da conta própria com a Riot ao abrir a aba Histórico.

O pedido (API) só cria uma execução QUEUED, respeitando uma execução ativa por conta e um
intervalo mínimo entre execuções. O worker faz a rede fora da transação: resolve a conta
própria, lista os IDs ranqueados recentes e enfileira IMPORT (modo RIOT) só para as partidas
novas; as importações seguem o caminho normal do worker, com limitador e cooldown. Nunca
grava PUUID, Riot ID ou IDs de partida na execução: só contagens e códigos de erro.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from riftlab.config import Settings
from riftlab.history_sync.models import HistorySyncRunModel as Run
from riftlab.ingestion.application import request_import
from riftlab.ingestion.domain import ImportJobState, ImportMode
from riftlab.ingestion.infrastructure.riot_account import (
    ALLOWED_PLATFORM,
    ALLOWED_REGIONAL_ROUTING,
    RiotAccountConfig,
    RiotAccountConfigError,
    load_riot_account,
)
from riftlab.ingestion.infrastructure.riot_rate_limiter import RiotRateLimiter
from riftlab.ingestion.infrastructure.riot_transport import RiotFetchBudget, RiotHttpTransport
from riftlab.ingestion.models import MatchModel
from riftlab.ingestion.purge import is_purged
from riftlab.ingestion.repository import SqlAlchemyIngestionRepository
from riftlab.players.models import PlayerProfileModel
from riftlab.ranked.domain import account_key
from riftlab.shared.db import unit_of_work
from riftlab.shared.errors import SourceError, SourceRateLimitedError

SessionFactory = async_sessionmaker[AsyncSession]
ACTIVE = ("QUEUED", "RUNNING")
AUTH_FAILED = "RIOT_AUTH_FAILED"
# Execução RUNNING sem término depois disso (queda do worker) não bloqueia novos pedidos.
STALE_AFTER = timedelta(minutes=10)
# Folga sobre o início da última sincronização bem-sucedida para não perder partidas.
OVERLAP = timedelta(hours=1)
# Trocar a chave exige reiniciar o processo: falha de autenticação anterior a ele não segura.
PROCESS_STARTED_AT = datetime.now(UTC)


def _account_key(settings: Settings, account: RiotAccountConfig | None = None) -> str | None:
    """Chave da conta pedida (perfil buscado, CC-1101) ou, sem ela, da conta configurada."""
    if account is None:
        try:
            account = load_riot_account(Path(settings.ranked_account_file))
        except (RiotAccountConfigError, OSError):
            return None
    return account_key(account, settings.subject_hash_secret)


async def _account_for(
    factory: SessionFactory, settings: Settings, key: str
) -> RiotAccountConfig | None:
    """Conta de uma execução: a configurada, se for a mesma chave; senão o perfil guardado."""
    try:
        configured = load_riot_account(Path(settings.ranked_account_file))
    except (RiotAccountConfigError, OSError):
        configured = None
    if configured is not None and account_key(configured, settings.subject_hash_secret) == key:
        return configured
    async with factory() as session:
        profile = await session.scalar(
            select(PlayerProfileModel).where(PlayerProfileModel.account_key == key)
        )
    if profile is None:
        return None
    return RiotAccountConfig(
        profile.game_name, profile.tag_line, ALLOWED_PLATFORM, ALLOWED_REGIONAL_ROUTING
    )


def next_allowed_at(run: Run, settings: Settings) -> datetime | None:
    """Quando um novo pedido pode criar outra execução; None enquanto há uma ativa."""
    if run.state in ACTIVE or run.finished_at is None:
        return None
    wait = settings.history_sync_cooldown_seconds
    if run.error_code == AUTH_FAILED and run.finished_at >= PROCESS_STARTED_AT:
        # Chave rejeitada: esperar mais, em vez de repetir a mesma falha a cada visita.
        wait = max(wait, settings.history_sync_auth_cooldown_seconds)
    elif run.retry_after_seconds:
        wait = max(wait, run.retry_after_seconds)
    return run.finished_at + timedelta(seconds=wait)


def _status(run: Run | None, settings: Settings, reason: str | None = None) -> dict[str, Any]:
    if run is None:
        return {"state": "IDLE", "reason": reason}
    return {
        "state": run.state,
        # Falha dentro do intervalo mínimo mantém o código do erro (a tela explica a causa).
        "reason": run.error_code or reason,
        "requestedAt": run.requested_at,
        "finishedAt": run.finished_at,
        "nextAllowedAt": next_allowed_at(run, settings),
        "listed": run.listed,
        "newEnqueued": run.new_enqueued,
        "alreadyKnown": run.already_known,
        "skippedPurged": run.skipped_purged,
    }


def _unavailable(reason: str) -> dict[str, Any]:
    return {"state": "UNAVAILABLE", "reason": reason}


async def _latest(session: AsyncSession, key: str) -> Run | None:
    return (
        await session.scalars(
            select(Run)
            .where(Run.account_key == key)
            .order_by(Run.requested_at.desc(), Run.id.desc())
            .limit(1)
        )
    ).first()


async def sync_status(
    factory: SessionFactory, settings: Settings, *, account: RiotAccountConfig | None = None
) -> dict[str, Any]:
    if not settings.riot_api_key:
        return _unavailable("RIOT_KEY_MISSING")
    key = _account_key(settings, account)
    if key is None:
        return _unavailable("ACCOUNT_NOT_CONFIGURED")
    async with factory() as session:
        return _status(await _latest(session, key), settings)


async def request_sync(
    factory: SessionFactory,
    settings: Settings,
    *,
    now: datetime | None = None,
    account: RiotAccountConfig | None = None,
) -> tuple[dict[str, Any], bool]:
    """Devolve (estado, criou?). Sem chave ou sem conta não cria nada. `account` é o perfil
    buscado (CC-1101); sem ela, a conta configurada."""
    if not settings.riot_api_key:
        return _unavailable("RIOT_KEY_MISSING"), False
    key = _account_key(settings, account)
    if key is None:
        return _unavailable("ACCOUNT_NOT_CONFIGURED"), False
    now = now or datetime.now(UTC)
    try:
        async with unit_of_work(factory) as session:
            latest = await _latest(session, key)
            if latest is not None and latest.state in ACTIVE:
                if (latest.started_at or latest.requested_at) > now - STALE_AFTER:
                    return _status(latest, settings), False
                latest.state, latest.error_code, latest.finished_at = "FAILED", "SYNC_STALE", now
                await session.flush()
            elif latest is not None:
                allowed = next_allowed_at(latest, settings)
                if allowed is not None and allowed > now:
                    return _status(latest, settings, "COOLDOWN"), False
            run = Run(account_key=key, state="QUEUED", requested_at=now)
            session.add(run)
            await session.flush()
            created = _status(run, settings)
    except IntegrityError:
        # Corrida com outro pedido: devolve a execução ativa da vencedora.
        async with factory() as session:
            return _status(await _latest(session, key), settings), False
    return created, True


def _transport(settings: Settings) -> RiotHttpTransport:
    assert settings.riot_api_key is not None
    return RiotHttpTransport(
        settings.riot_api_key,
        # Conta + uma listagem; nada de partidas aqui (as importações têm orçamento próprio).
        RiotFetchBudget(max_matches=0, max_requests=3, max_total_seconds=60.0, stop_on_429=True),
        rate_limiter=RiotRateLimiter(),
    )


async def _finish(factory: SessionFactory, run_id: Any, **fields: Any) -> None:
    async with unit_of_work(factory) as session:
        run = await session.get(Run, run_id)
        assert run is not None
        for name, value in fields.items():
            setattr(run, name, value)
        run.finished_at = datetime.now(UTC)


async def process_next_history_sync(
    factory: SessionFactory,
    settings: Settings,
    *,
    transport: RiotHttpTransport | None = None,
    now: datetime | None = None,
) -> bool:
    """Um passo do worker: reserva a execução QUEUED mais antiga, se houver."""
    now = now or datetime.now(UTC)
    async with unit_of_work(factory) as session:
        run = (
            await session.scalars(
                select(Run)
                .where(Run.state == "QUEUED")
                .order_by(Run.requested_at, Run.id)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
        ).first()
        if run is None:
            return False
        run.state, run.started_at = "RUNNING", now
        run_id, key = run.id, run.account_key
        previous = (
            await session.scalars(
                select(Run.started_at)
                .where(Run.account_key == key, Run.state == "SUCCEEDED")
                .order_by(Run.started_at.desc())
                .limit(1)
            )
        ).first()
    since = (
        previous - OVERLAP
        if previous
        else now - timedelta(days=settings.history_sync_lookback_days)
    )
    try:
        account = await _account_for(factory, settings, key)
        if account is None or not settings.riot_api_key:
            await _finish(factory, run_id, state="FAILED", error_code="ACCOUNT_CHANGED")
            return True
        own = transport is None
        client = transport or _transport(settings)
        try:
            puuid = await client.resolve_account_puuid(account.game_name, account.tag_line)
            ids = await client.list_match_ids_by_puuid(
                puuid,
                queue=None,
                start=0,
                count=settings.history_sync_max_ids,
                start_time=int(since.timestamp()),
                match_type="ranked",
            )
        finally:
            if own:
                await client.aclose()
    except RiotAccountConfigError:
        await _finish(factory, run_id, state="FAILED", error_code="ACCOUNT_NOT_CONFIGURED")
        return True
    except SourceError as exc:
        retry = exc.retry_after_seconds if isinstance(exc, SourceRateLimitedError) else None
        await _finish(
            factory, run_id, state="FAILED", error_code=exc.code[:40], retry_after_seconds=retry
        )
        return True
    known = purged = enqueued = 0
    for match_id in ids:
        async with factory() as session:
            exists = await session.get(MatchModel, match_id) is not None
            latest = await SqlAlchemyIngestionRepository(session).get_latest_import_job(
                match_id, ImportMode.RIOT
            )
        if exists or (latest is not None and latest.state != ImportJobState.FAILED):
            known += 1
        elif await is_purged(factory, match_id, settings.subject_hash_secret):
            purged += 1  # expurgo pedido não volta por sincronização (CC-603)
        else:
            await request_import(factory, match_id, ImportMode.RIOT)
            enqueued += 1
    await _finish(
        factory,
        run_id,
        state="SUCCEEDED",
        listed=len(ids),
        already_known=known,
        skipped_purged=purged,
        new_enqueued=enqueued,
    )
    return True
