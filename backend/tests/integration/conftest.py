from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig
from pipeline_helpers import reset_match
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from riftlab.config import Settings
from riftlab.db import build_engine
from riftlab.shared.db import build_session_factory

BACKEND_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session")
def _migrated_database() -> None:
    """Aplica migrations reais (alembic upgrade head) contra RIFTLAB_DATABASE_URL
    antes dos testes de persistência. Falha se o banco exigido não estiver
    acessível; nunca faz skip silencioso que produza aceite falso."""
    config = AlembicConfig(str(BACKEND_ROOT / "alembic.ini"))
    alembic_command.upgrade(config, "head")


@pytest_asyncio.fixture
async def session_factory(
    _migrated_database: None,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    settings = Settings()
    engine = build_engine(settings.database_url)
    try:
        yield build_session_factory(engine)
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def _demo_match(session_factory, request) -> AsyncIterator[str]:
    """Reserva uma identidade DEMO-000X para o teste: limpa antes (recupera
    de uma rodada anterior interrompida) e SEMPRE depois, mesmo em falha de
    asserção (finally) — nunca deixa resíduo SUCCEEDED para os arquivos-
    irmãos que reusam a mesma identidade fixa dentro do mesmo processo
    pytest."""
    match_id: str = request.param
    await reset_match(session_factory, match_id)
    try:
        yield match_id
    finally:
        await reset_match(session_factory, match_id)
