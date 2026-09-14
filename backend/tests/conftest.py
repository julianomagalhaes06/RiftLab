from collections.abc import AsyncIterator

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from riftlab.api.app import create_app
from riftlab.config import Settings


async def _client_for(settings: Settings) -> AsyncIterator[AsyncClient]:
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


@pytest_asyncio.fixture
async def app_client(monkeypatch) -> AsyncIterator[AsyncClient]:
    """Cliente contra a app com a configuracao real do ambiente (DATABASE_URL de
    teste fornecido pelo executor: compose.test.yaml ou servico do CI)."""
    monkeypatch.delenv("RIFTLAB_RIOT_API_KEY", raising=False)
    async for client in _client_for(Settings()):
        yield client


@pytest_asyncio.fixture
async def app_client_with_unreachable_db() -> AsyncIterator[AsyncClient]:
    """Cliente forcado a falhar a conexao (host loopback em porta sem listener) e
    timeout curto, para exercitar o caminho 503 de forma deterministica, sem
    depender de derrubar infraestrutura real durante o teste."""
    settings = Settings(
        database_url="postgresql+asyncpg://riftlab:riftlab@127.0.0.1:1/riftlab_unreachable",
        database_ready_timeout_seconds=1.0,
    )
    async for client in _client_for(settings):
        yield client
