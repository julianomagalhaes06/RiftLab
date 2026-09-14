from __future__ import annotations

import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine


def build_engine(database_url: str) -> AsyncEngine:
    """Engine sem pool aberto de longa duracao; CC-103 adiciona tabelas/migrations."""
    return create_async_engine(database_url, pool_pre_ping=True, pool_size=5)


async def ping(engine: AsyncEngine, timeout_seconds: float) -> bool:
    """Verifica conectividade com SELECT 1 sob timeout curto. Retorna False para
    qualquer falha de rede/autenticacao/timeout; o chamador decide o codigo de erro
    sem expor detalhe interno ao cliente."""
    try:
        async with asyncio.timeout(timeout_seconds):
            async with engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
