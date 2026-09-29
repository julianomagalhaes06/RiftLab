from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from riftlab.config import Settings
from riftlab.ingestion.infrastructure.riot_account import RiotAccountConfigError, load_riot_account
from riftlab.ingestion.infrastructure.riot_rate_limiter import RiotRateLimiter
from riftlab.ingestion.infrastructure.riot_transport import RiotFetchBudget, RiotHttpTransport
from riftlab.ingestion.infrastructure.subject_hash import compute_real_subject_hash
from riftlab.ingestion.models import MatchModel, ParticipantModel
from riftlab.ranked.domain import (
    FLEX_QUEUE,
    SEASON_PATTERN,
    SOLO_QUEUE,
    account_key,
    parse_league_entries,
)
from riftlab.ranked.models import RankedObservationModel as Observation
from riftlab.shared.db import read_only_unit_of_work, unit_of_work
from riftlab.shared.errors import SourceError

FRESH_FOR = timedelta(hours=24)


def _account_path(settings: Settings) -> Path:
    return Path(settings.ranked_account_file)


def _identity(settings: Settings) -> str | None:
    try:
        return account_key(load_riot_account(_account_path(settings)), settings.subject_hash_secret)
    except (RiotAccountConfigError, OSError):
        return None


async def refresh_ranked(
    factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    season: str,
    *,
    transport: RiotHttpTransport | None = None,
    captured_at: datetime | None = None,
) -> dict[str, Any]:
    """Explicit administrative refresh. Network finishes before the database transaction."""
    if not SEASON_PATTERN.fullmatch(season):
        raise ValueError("temporada inválida")
    account = load_riot_account(_account_path(settings))
    key = account_key(account, settings.subject_hash_secret)
    if key is None or not settings.riot_api_key:
        raise ValueError("chave Riot ou segredo HMAC ausente")
    own_transport = transport is None
    if transport is None:
        transport = RiotHttpTransport(
            settings.riot_api_key,
            RiotFetchBudget(max_matches=0, max_requests=8, max_total_seconds=90),
            rate_limiter=RiotRateLimiter(),
        )
    try:
        puuid = await transport.resolve_account_puuid(account.game_name, account.tag_line)
        entries = parse_league_entries(await transport.fetch_league_entries_by_puuid(puuid), puuid)
    except SourceError as exc:
        stamp = captured_at or datetime.now(UTC)
        async with unit_of_work(factory) as session:
            session.add(
                Observation(
                    account_key=key,
                    queue=SOLO_QUEUE,
                    season=season,
                    captured_at=stamp,
                    state="FAILED",
                    error_code=exc.code,
                    origin="RIOT_LEAGUE_V4",
                )
            )
        raise
    finally:
        if own_transport:
            await transport.aclose()
    stamp = captured_at or datetime.now(UTC)
    subject_hash = compute_real_subject_hash(puuid, settings.subject_hash_secret)
    async with unit_of_work(factory) as session:
        for queue in (SOLO_QUEUE, FLEX_QUEUE):
            entry = entries.get(queue)
            session.add(
                Observation(
                    account_key=key,
                    subject_hash=subject_hash,
                    queue=queue,
                    season=season,
                    captured_at=stamp,
                    state="READY" if entry else "UNRANKED",
                    tier=entry.tier if entry else None,
                    division=entry.division if entry else None,
                    league_points=entry.league_points if entry else None,
                    wins=entry.wins if entry else None,
                    losses=entry.losses if entry else None,
                    origin="RIOT_LEAGUE_V4",
                )
            )
    return {
        "state": "READY" if entries.get(SOLO_QUEUE) else "UNRANKED",
        "queue": SOLO_QUEUE,
        "season": season,
    }


async def ranked_me(
    factory: async_sessionmaker[AsyncSession], settings: Settings, *, now: datetime | None = None
) -> dict[str, Any]:
    key = _identity(settings)
    if key is None:
        return {
            "status": "UNAVAILABLE",
            "reason": "ACCOUNT_OR_SECRET_MISSING",
            "position": None,
            "change": None,
        }
    async with read_only_unit_of_work(factory) as session:
        latest = await session.scalar(
            select(Observation)
            .where(
                Observation.account_key == key,
                Observation.queue == SOLO_QUEUE,
            )
            .order_by(Observation.captured_at.desc(), Observation.id.desc())
            .limit(1)
        )
        if latest is None:
            return {
                "status": "UNAVAILABLE",
                "reason": "NO_OBSERVATION",
                "position": None,
                "change": None,
            }
        successes = (
            await session.scalars(
                select(Observation)
                .where(
                    Observation.account_key == key,
                    Observation.queue == SOLO_QUEUE,
                    Observation.season == latest.season,
                    Observation.state != "FAILED",
                )
                .order_by(Observation.captured_at.desc(), Observation.id.desc())
                .limit(2)
            )
        ).all()
        if not successes:
            return {
                "status": "UNAVAILABLE",
                "reason": latest.error_code,
                "position": None,
                "change": None,
            }
        current = successes[0]
        previous = successes[1] if len(successes) > 1 else None
        status = current.state
        reason = None
        if latest.state == "FAILED":
            status, reason = "STALE", latest.error_code
        elif not settings.riot_api_key:
            status, reason = "STALE", "RIOT_KEY_MISSING"
        elif (now or datetime.now(UTC)) - current.captured_at > FRESH_FOR:
            status, reason = "STALE", "OBSERVATION_OLD"
        change = await _change(session, previous, current) if previous else None
        return {
            "status": status,
            "reason": reason,
            "position": _position(current),
            "change": change,
        }


def _position(event: Observation) -> dict[str, Any]:
    return {
        "queue": event.queue,
        "season": event.season,
        "observedAt": event.captured_at,
        "origin": event.origin,
        "state": event.state,
        "tier": event.tier,
        "division": event.division,
        "leaguePoints": event.league_points,
        "wins": event.wins,
        "losses": event.losses,
    }


async def _change(
    session: AsyncSession, previous: Observation, current: Observation
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "since": previous.captured_at,
        "until": current.captured_at,
        "kind": "NOT_COMPARABLE",
        "leaguePointsDelta": None,
        "ownMatchAssociation": "NONE",
    }
    if previous.state == "READY" and current.state == "READY":
        if (current.wins or 0) < (previous.wins or 0) or (current.losses or 0) < (
            previous.losses or 0
        ):
            result["kind"] = "CORRECTION"
        elif (previous.tier, previous.division) != (current.tier, current.division):
            result["kind"] = "TRANSITION"
        else:
            result["kind"] = "SAME_DIVISION"
            result["leaguePointsDelta"] = (current.league_points or 0) - (
                previous.league_points or 0
            )
    # A Riot não fornece no League-V4 o PDL por partida. A associação é só do intervalo.
    # Nunca usar hora apenas: exigir subject_hash próprio, fila Solo e partida concluída.
    # GET é estritamente banco: não resolve PUUID. O hash só pode vir de um
    # vínculo observado na coleta; guardado como HMAC no próprio snapshot.
    if (
        current.subject_hash is None
        or previous.subject_hash != current.subject_hash
        or current.captured_at <= previous.captured_at
    ):
        return result
    count = await session.scalar(
        select(func.count(func.distinct(MatchModel.match_id)))
        .select_from(MatchModel)
        .join(ParticipantModel, ParticipantModel.match_id == MatchModel.match_id)
        .where(
            ParticipantModel.subject_hash == current.subject_hash,
            MatchModel.queue == 420,
            MatchModel.start_at + (MatchModel.duration_seconds * text("INTERVAL '1 second'"))
            > previous.captured_at,
            MatchModel.start_at + (MatchModel.duration_seconds * text("INTERVAL '1 second'"))
            <= current.captured_at,
        )
    )
    result["ownMatchAssociation"] = (
        "ONE" if count == 1 else "MULTIPLE" if count and count > 1 else "NONE"
    )
    return result
