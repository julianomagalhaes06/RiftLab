"""Perfil de jogador por Riot ID (CC-1101), como em sites de estatísticas: qualquer Riot ID pode
ser consultado. Account-V1 dá o PUUID e o Riot ID canônico; Summoner-V4 (BR1), o ícone e o nível;
League-V4 (BR1), o elo. O perfil fica guardado com o HMAC do Riot ID e o HMAC do PUUID; o PUUID
nunca é gravado nem sai daqui. Consulta à Riot só quando o perfil não existe, está velho ou o
usuário pede para atualizar.
"""

from __future__ import annotations

import unicodedata
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from riftlab.catalog.models import CatalogVersionModel
from riftlab.config import Settings
from riftlab.ingestion.infrastructure.riot_account import (
    ALLOWED_PLATFORM,
    ALLOWED_REGIONAL_ROUTING,
    RiotAccountConfig,
)
from riftlab.ingestion.infrastructure.riot_rate_limiter import RiotRateLimiter
from riftlab.ingestion.infrastructure.riot_transport import RiotFetchBudget, RiotHttpTransport
from riftlab.ingestion.infrastructure.subject_hash import compute_real_subject_hash
from riftlab.players.models import PlayerProfileModel as Profile
from riftlab.ranked.domain import FLEX_QUEUE, SOLO_QUEUE, account_key, parse_league_entries
from riftlab.ranked.models import RankedObservationModel as Observation
from riftlab.shared.db import read_only_unit_of_work, unit_of_work
from riftlab.shared.errors import (
    NotFoundError,
    PlayerLookupError,
    SourceAuthError,
    SourceError,
    SourceNotFoundError,
    SourceRateLimitedError,
)
from riftlab.shared.numeric import patch_order_key

SessionFactory = async_sessionmaker[AsyncSession]
# Perfil consultado há menos disso é devolvido sem nova consulta (o botão Atualizar força).
CACHE_FOR = timedelta(minutes=10)
GAME_NAME_MAX = 16
TAG_LINE_MIN, TAG_LINE_MAX = 3, 5
# Um limitador por processo da API: as buscas de todos os usuários dividem a mesma janela.
_LIMITER = RiotRateLimiter()


def normalize_riot_id(game_name: str, tag_line: str) -> tuple[str, str]:
    """Riot ID digitado (Nome e TAG): espaços nas pontas fora, NFC; `#` não pode aparecer.
    Inválido ⇒ PlayerLookupError INVALID_RIOT_ID (nada é consultado)."""
    name = unicodedata.normalize("NFC", game_name).strip()
    tag = unicodedata.normalize("NFC", tag_line).strip().lstrip("#")
    if (
        not name
        or len(name) > GAME_NAME_MAX
        or "#" in name
        or not TAG_LINE_MIN <= len(tag) <= TAG_LINE_MAX
        or not tag.isalnum()
        or any(unicodedata.category(c).startswith("C") for c in name)
    ):
        raise PlayerLookupError("INVALID_RIOT_ID")
    return name, tag


def _key(settings: Settings, game_name: str, tag_line: str) -> str:
    key = account_key(
        RiotAccountConfig(game_name, tag_line, ALLOWED_PLATFORM, ALLOWED_REGIONAL_ROUTING),
        settings.subject_hash_secret,
    )
    if key is None:
        raise PlayerLookupError("SECRET_MISSING")
    return key


async def _data_dragon_version(session: AsyncSession) -> str | None:
    """Versão do Data Dragon do patch real mais recente do catálogo (ícone de invocador)."""
    rows = (
        await session.execute(
            select(CatalogVersionModel.game_patch, CatalogVersionModel.data_dragon_version)
        )
    ).all()
    ranked = sorted(
        ((key, version) for patch, version in rows if (key := patch_order_key(patch)) is not None),
        reverse=True,
    )
    return ranked[0][1] if ranked else None


def _position(event: Observation | None) -> dict[str, Any] | None:
    if event is None:
        return None
    return {
        "queue": event.queue,
        "state": event.state,
        "tier": event.tier,
        "division": event.division,
        "leaguePoints": event.league_points,
        "wins": event.wins,
        "losses": event.losses,
        "observedAt": event.captured_at,
    }


async def _view(
    session: AsyncSession, profile: Profile, stale_reason: str | None
) -> dict[str, Any]:
    ranked: dict[str, Any] = {}
    for queue in (SOLO_QUEUE, FLEX_QUEUE):
        latest = await session.scalar(
            select(Observation)
            .where(
                Observation.account_key == profile.account_key,
                Observation.queue == queue,
                Observation.state != "FAILED",
            )
            .order_by(Observation.captured_at.desc(), Observation.id.desc())
            .limit(1)
        )
        ranked[queue] = _position(latest)
    return {
        "id": profile.id,
        "gameName": profile.game_name,
        "tagLine": profile.tag_line,
        "profileIconId": profile.profile_icon_id,
        "summonerLevel": profile.summoner_level,
        "refreshedAt": profile.refreshed_at,
        "dataDragonVersion": await _data_dragon_version(session),
        "solo": ranked[SOLO_QUEUE],
        "flex": ranked[FLEX_QUEUE],
        "staleReason": stale_reason,
    }


def _transport(settings: Settings) -> RiotHttpTransport:
    assert settings.riot_api_key is not None
    return RiotHttpTransport(
        settings.riot_api_key,
        # Conta + invocador + elo; nada de partidas (a sincronização tem orçamento próprio).
        RiotFetchBudget(max_matches=0, max_requests=3, max_total_seconds=20.0, stop_on_429=True),
        rate_limiter=_LIMITER,
    )


async def get_player(factory: SessionFactory, player_id: uuid.UUID) -> dict[str, Any]:
    async with read_only_unit_of_work(factory) as session:
        profile = await session.get(Profile, player_id)
        if profile is None:
            raise NotFoundError("jogador não encontrado")
        return await _view(session, profile, None)


async def lookup_player(
    factory: SessionFactory,
    settings: Settings,
    game_name: str,
    tag_line: str,
    *,
    refresh: bool = False,
    transport: RiotHttpTransport | None = None,
    now: datetime | None = None,
    season: str | None = None,
) -> dict[str, Any]:
    """Perfil do Riot ID. Guardado e recente (ou sem chave da Riot): devolve o guardado.
    Senão consulta a Riot (3 requisições) e grava perfil e elo."""
    name, tag = normalize_riot_id(game_name, tag_line)
    key = _key(settings, name, tag)
    now = now or datetime.now(UTC)
    async with read_only_unit_of_work(factory) as session:
        stored = await session.scalar(select(Profile).where(Profile.account_key == key))
        if stored is not None and not settings.riot_api_key:
            return await _view(session, stored, "RIOT_KEY_MISSING")
        if stored is not None and not refresh and now - stored.refreshed_at < CACHE_FOR:
            return await _view(session, stored, None)
    if not settings.riot_api_key:
        raise PlayerLookupError("RIOT_KEY_MISSING")

    own = transport is None
    client = transport or _transport(settings)
    try:
        puuid, canonical_name, canonical_tag = await client.resolve_account(name, tag)
        try:
            icon, level = await client.fetch_summoner_by_puuid(puuid)
        except SourceNotFoundError as exc:
            raise PlayerLookupError("PLAYER_NOT_ON_PLATFORM") from exc
        entries = parse_league_entries(await client.fetch_league_entries_by_puuid(puuid), puuid)
    except SourceNotFoundError as exc:
        raise PlayerLookupError("PLAYER_NOT_FOUND") from exc
    except SourceAuthError as exc:
        raise PlayerLookupError("RIOT_AUTH_FAILED") from exc
    except SourceRateLimitedError as exc:
        raise PlayerLookupError(
            "RIOT_RATE_LIMITED", retry_after_seconds=exc.retry_after_seconds
        ) from exc
    except SourceError as exc:
        raise PlayerLookupError("UPSTREAM_UNAVAILABLE") from exc
    finally:
        if own:
            await client.aclose()

    subject = compute_real_subject_hash(puuid, settings.subject_hash_secret)
    key = _key(settings, canonical_name, canonical_tag)
    async with unit_of_work(factory) as session:
        profile = await session.scalar(
            select(Profile).where(Profile.account_key == key).with_for_update()
        )
        if profile is None:
            profile = Profile(account_key=key)
            session.add(profile)
        profile.game_name, profile.tag_line = canonical_name, canonical_tag
        profile.subject_hash = subject
        profile.profile_icon_id, profile.summoner_level = icon, level
        profile.refreshed_at = now
        for queue in (SOLO_QUEUE, FLEX_QUEUE):
            entry = entries.get(queue)
            session.add(
                Observation(
                    account_key=key,
                    subject_hash=subject,
                    queue=queue,
                    season=season or str(now.year),
                    captured_at=now,
                    state="READY" if entry else "UNRANKED",
                    tier=entry.tier if entry else None,
                    division=entry.division if entry else None,
                    league_points=entry.league_points if entry else None,
                    wins=entry.wins if entry else None,
                    losses=entry.losses if entry else None,
                    origin="RIOT_LEAGUE_V4",
                )
            )
        await session.flush()
        return await _view(session, profile, None)


async def profile_account(factory: SessionFactory, player_id: uuid.UUID) -> RiotAccountConfig:
    """Riot ID guardado do perfil, no formato da conta configurada (sincronização)."""
    async with read_only_unit_of_work(factory) as session:
        profile = await session.get(Profile, player_id)
        if profile is None:
            raise NotFoundError("jogador não encontrado")
        return RiotAccountConfig(
            profile.game_name, profile.tag_line, ALLOWED_PLATFORM, ALLOWED_REGIONAL_ROUTING
        )


async def profile_subject(factory: SessionFactory, player_id: uuid.UUID) -> str:
    async with read_only_unit_of_work(factory) as session:
        profile = await session.get(Profile, player_id)
        if profile is None:
            raise NotFoundError("jogador não encontrado")
        return profile.subject_hash
