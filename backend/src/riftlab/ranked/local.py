"""Participante da conta local nas partidas importadas (histórico).

Mesma regra do Painel: a conta configurada em `ranked_account_file` vira uma chave
HMAC local, e o `subject_hash` vem da observação League-V4 mais recente dessa chave.
Nada disso sai da API: quem chama só recebe o hash para comparar no banco."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from riftlab.config import Settings
from riftlab.ingestion.infrastructure.riot_account import RiotAccountConfigError, load_riot_account
from riftlab.ranked.domain import account_key
from riftlab.ranked.models import RankedObservationModel as Observation


async def local_subject_hash(session: AsyncSession, settings: Settings) -> str | None:
    """None sem conta configurada, sem segredo ou sem observação real da conta."""
    try:
        key = account_key(
            load_riot_account(Path(settings.ranked_account_file)), settings.subject_hash_secret
        )
    except (OSError, RiotAccountConfigError):
        return None
    if key is None:
        return None
    return await session.scalar(
        select(Observation.subject_hash)
        .where(
            Observation.account_key == key,
            Observation.origin == "RIOT_LEAGUE_V4",
            Observation.subject_hash.is_not(None),
        )
        .order_by(Observation.captured_at.desc(), Observation.id.desc())
        .limit(1)
    )
