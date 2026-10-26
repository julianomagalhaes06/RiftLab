"""Lápide do expurgo (CC-603), checada dentro do pipeline de importação (CC-1216).

Módulo folha, sem depender do expurgo nem do serviço de importação: os dois o usam. Uma partida
RIOT expurgada não volta por caminho nenhum (rota, sincronização, `cc collect import`, retry,
job já na fila): a criação do job e a transação que persiste a importação conferem a lápide
sob o mesmo lock consultivo que o expurgo toma, então um não passa pelo outro no meio. DEMO
pode ser reimportada (fixtures sintéticas; a lápide guarda a última).
"""

from __future__ import annotations

import hashlib
import hmac

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from riftlab.ingestion.domain import ImportMode
from riftlab.ingestion.models import PurgeTombstoneModel
from riftlab.shared.errors import ConflictError

MATCH_PURGED = "MATCH_PURGED"
# Id que fica nos jobs de uma partida expurgada, no lugar do id real.
PURGED_PREFIX = "PURGED-"


class MatchPurgedError(ConflictError):
    """Lápide RIOT presente: a borda HTTP responde 409 MATCH_PURGED."""

    def __init__(self) -> None:
        super().__init__(MATCH_PURGED)


def match_ref(match_id: str, secret: str | None) -> str:
    """Referência irreversível do id para a lápide (HMAC com o segredo local)."""
    key = (secret or "riftlab-local-purge").encode()
    return hmac.new(key, f"riftlab-purge-v1:{match_id}".encode(), hashlib.sha256).hexdigest()


def scrubbed_match_id(match_id: str, secret: str | None) -> str:
    return PURGED_PREFIX + match_ref(match_id, secret)[:24]


async def lock_match_ref(session: AsyncSession, ref: str) -> None:
    """Lock da transação que serializa expurgo, criação de job e persistência da mesma partida."""
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": "purge:" + ref}
    )


async def ensure_not_purged(
    session: AsyncSession, match_id: str, mode: ImportMode, secret: str | None
) -> None:
    """Dentro da transação de quem cria ou persiste: levanta MatchPurgedError se há lápide."""
    if mode != ImportMode.RIOT:
        return
    ref = match_ref(match_id, secret)
    await lock_match_ref(session, ref)
    found = await session.scalar(
        select(PurgeTombstoneModel.id).where(PurgeTombstoneModel.match_ref == ref)
    )
    if found is not None:
        raise MatchPurgedError()
