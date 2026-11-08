"""Vínculo da conta Riot pelo desafio do ícone de perfil (card #12).

Sem o login da Riot (RSO exige chave de produção), a posse da conta é comprovada assim: o
sistema sorteia um dos ícones iniciais (0 a 28, que toda conta tem) diferente do atual; a
pessoa o coloca no perfil dentro do prazo e pede a verificação; Summoner-V4 confirma o ícone.
Cada etapa usa no máximo duas requisições (Account-V1 e Summoner-V4).
"""

from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from riftlab.auth.models import RiotLinkChallengeModel, RiotLinkModel
from riftlab.config import Settings
from riftlab.ingestion.infrastructure.riot_account import (
    ALLOWED_PLATFORM,
    ALLOWED_REGIONAL_ROUTING,
    RiotAccountConfig,
)
from riftlab.ingestion.infrastructure.riot_rate_limiter import RiotRateLimiter
from riftlab.ingestion.infrastructure.riot_transport import RiotFetchBudget, RiotHttpTransport
from riftlab.ingestion.infrastructure.subject_hash import compute_real_subject_hash
from riftlab.players.application import normalize_riot_id
from riftlab.ranked.domain import account_key
from riftlab.shared.db import unit_of_work
from riftlab.shared.errors import (
    PlayerLookupError,
    SourceAuthError,
    SourceError,
    SourceNotFoundError,
    SourceRateLimitedError,
)

SessionFactory = async_sessionmaker[AsyncSession]
STARTER_ICONS = range(0, 29)
CHALLENGE_TTL = timedelta(minutes=20)
_LIMITER = RiotRateLimiter()


class RiotLinkError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _transport(settings: Settings) -> RiotHttpTransport:
    assert settings.riot_api_key is not None
    return RiotHttpTransport(
        settings.riot_api_key,
        RiotFetchBudget(max_matches=0, max_requests=2, max_total_seconds=20.0, stop_on_429=True),
        rate_limiter=_LIMITER,
    )


async def _current_icon(
    settings: Settings, game_name: str, tag_line: str, transport: Any | None
) -> tuple[str, str, str, int]:
    """(puuid, gameName canônico, tagLine canônico, ícone atual), com os erros da busca."""
    own = transport is None
    client = transport or _transport(settings)
    try:
        puuid, name, tag = await client.resolve_account(game_name, tag_line)
        try:
            icon, _level = await client.fetch_summoner_by_puuid(puuid)
        except SourceNotFoundError as exc:
            raise PlayerLookupError("PLAYER_NOT_ON_PLATFORM") from exc
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
    return puuid, name, tag, icon


def _key(settings: Settings, game_name: str, tag_line: str) -> str:
    key = account_key(
        RiotAccountConfig(game_name, tag_line, ALLOWED_PLATFORM, ALLOWED_REGIONAL_ROUTING),
        settings.subject_hash_secret,
    )
    if key is None:
        raise PlayerLookupError("SECRET_MISSING")
    return key


def _ready(settings: Settings) -> None:
    if not settings.riot_api_key:
        raise PlayerLookupError("RIOT_KEY_MISSING")
    if not settings.subject_hash_secret:
        raise PlayerLookupError("SECRET_MISSING")


async def link_state(
    factory: SessionFactory, user_id: uuid.UUID, *, now: datetime | None = None
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    async with factory() as session:
        link = await session.get(RiotLinkModel, user_id)
        if link is not None:
            return {
                "state": "LINKED",
                "gameName": link.game_name,
                "tagLine": link.tag_line,
                "linkedAt": link.linked_at,
            }
        challenge = await session.get(RiotLinkChallengeModel, user_id)
    if challenge is not None and challenge.expires_at > now:
        return {
            "state": "PENDING",
            "gameName": challenge.game_name,
            "tagLine": challenge.tag_line,
            "iconId": challenge.icon_id,
            "expiresAt": challenge.expires_at,
        }
    return {"state": "NONE"}


async def start_challenge(
    factory: SessionFactory,
    settings: Settings,
    user_id: uuid.UUID,
    game_name: str,
    tag_line: str,
    *,
    transport: Any | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Novo desafio (substitui o anterior). Conta já vinculada a outro usuário ⇒ recusa."""
    _ready(settings)
    name, tag = normalize_riot_id(game_name, tag_line)
    _puuid, canonical_name, canonical_tag, icon = await _current_icon(
        settings, name, tag, transport
    )
    key = _key(settings, canonical_name, canonical_tag)
    now = now or datetime.now(UTC)
    async with unit_of_work(factory) as session:
        owner = await session.scalar(
            select(RiotLinkModel.user_id).where(RiotLinkModel.account_key == key)
        )
        if owner is not None and owner != user_id:
            raise RiotLinkError("ACCOUNT_ALREADY_LINKED")
        await session.execute(
            delete(RiotLinkChallengeModel).where(RiotLinkChallengeModel.user_id == user_id)
        )
        session.add(
            RiotLinkChallengeModel(
                user_id=user_id,
                account_key=key,
                game_name=canonical_name,
                tag_line=canonical_tag,
                icon_id=secrets.choice([i for i in STARTER_ICONS if i != icon]),
                created_at=now,
                expires_at=now + CHALLENGE_TTL,
            )
        )
    return await link_state(factory, user_id, now=now)


async def verify_challenge(
    factory: SessionFactory,
    settings: Settings,
    user_id: uuid.UUID,
    *,
    transport: Any | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Confere o ícone. Certo ⇒ vínculo gravado e desafio apagado; errado ⇒ o desafio segue
    valendo até expirar; expirado ⇒ apagado, pedir outro."""
    _ready(settings)
    now = now or datetime.now(UTC)
    async with factory() as session:
        challenge = await session.get(RiotLinkChallengeModel, user_id)
    if challenge is None:
        raise RiotLinkError("NO_CHALLENGE")
    if challenge.expires_at <= now:
        async with unit_of_work(factory) as session:
            await session.execute(
                delete(RiotLinkChallengeModel).where(RiotLinkChallengeModel.user_id == user_id)
            )
        raise RiotLinkError("CHALLENGE_EXPIRED")
    puuid, name, tag, icon = await _current_icon(
        settings, challenge.game_name, challenge.tag_line, transport
    )
    if icon != challenge.icon_id:
        raise RiotLinkError("ICON_MISMATCH")
    key = _key(settings, name, tag)
    if key != challenge.account_key:
        raise RiotLinkError("ICON_MISMATCH")  # Riot ID mudou no meio do desafio
    try:
        async with unit_of_work(factory) as session:
            await session.execute(delete(RiotLinkModel).where(RiotLinkModel.user_id == user_id))
            await session.execute(
                delete(RiotLinkChallengeModel).where(RiotLinkChallengeModel.user_id == user_id)
            )
            session.add(
                RiotLinkModel(
                    user_id=user_id,
                    account_key=key,
                    subject_hash=compute_real_subject_hash(puuid, settings.subject_hash_secret),
                    game_name=name,
                    tag_line=tag,
                    linked_at=now,
                )
            )
    except IntegrityError as exc:
        raise RiotLinkError("ACCOUNT_ALREADY_LINKED") from exc
    return await link_state(factory, user_id, now=now)


async def unlink(factory: SessionFactory, user_id: uuid.UUID) -> None:
    async with unit_of_work(factory) as session:
        await session.execute(delete(RiotLinkModel).where(RiotLinkModel.user_id == user_id))
        await session.execute(
            delete(RiotLinkChallengeModel).where(RiotLinkChallengeModel.user_id == user_id)
        )


async def linked_account(factory: SessionFactory, user_id: uuid.UUID) -> RiotAccountConfig | None:
    """Conta Riot vinculada ao usuário, no formato da conta configurada (card #13)."""
    async with factory() as session:
        link = await session.get(RiotLinkModel, user_id)
    if link is None:
        return None
    return RiotAccountConfig(
        link.game_name, link.tag_line, ALLOWED_PLATFORM, ALLOWED_REGIONAL_ROUTING
    )
