"""Participante da conta local nas partidas importadas (histórico).

Mesma regra do Painel: a conta configurada em `ranked_account_file` vira uma chave
HMAC local, e o `subject_hash` vem da observação League-V4 mais recente dessa chave.
Nada disso sai da API: quem chama só recebe o hash para comparar no banco."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from riftlab.config import Settings
from riftlab.ranked.domain import account_key
from riftlab.ranked.models import RankedObservationModel as Observation
from riftlab.shared.own_account import CONFIGURED, OwnAccount, resolve


async def local_subject_hash(
    session: AsyncSession, settings: Settings, account: OwnAccount = CONFIGURED
) -> str | None:
    """None sem conta (configurada ou vinculada), sem segredo ou sem observação real dela."""
    resolved = resolve(settings, account)
    if resolved is None:
        return None
    key = account_key(resolved, settings.subject_hash_secret)
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
