import ipaddress
import os
import socket
from collections.abc import AsyncIterator, Iterator
from typing import Any
from urllib.parse import urlsplit

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from riftlab.api.app import create_app
from riftlab.config import Settings

BLOCKED = "rede externa bloqueada nos testes"


def _allowed_addresses() -> set[str]:
    """Loopback e o host do banco de teste (db-test no compose.test.yaml, localhost na CI)."""
    allowed = {"127.0.0.1", "::1", "localhost"}
    host = urlsplit(os.environ.get("RIFTLAB_DATABASE_URL", "")).hostname
    if host:
        allowed.add(host)
        try:
            allowed |= {str(info[4][0]) for info in socket.getaddrinfo(host, None)}
        except OSError:
            pass
    return allowed


def _is_allowed(address: Any, allowed: set[str]) -> bool:
    if not isinstance(address, tuple):
        return True  # socket Unix local
    host = str(address[0])
    if host in allowed:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False  # nome externo


@pytest.fixture(autouse=True, scope="session")
def _no_external_network() -> Iterator[None]:
    """CC-601: nenhum teste alcança rede fora da máquina (Riot, Data Dragon, nuvem paga).

    Toda conexão para fora do loopback e do banco de teste falha antes de sair. Fontes externas
    são sempre simuladas (transportes falsos, fixtures DEMO). RIFTLAB_TEST_ALLOW_NETWORK=1
    desliga a guarda para um diagnóstico manual; a CI nunca define essa variável.
    """
    if os.environ.get("RIFTLAB_TEST_ALLOW_NETWORK") == "1":
        yield
        return
    allowed = _allowed_addresses()
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex

    def connect(self: socket.socket, address: Any) -> None:
        if not _is_allowed(address, allowed):
            raise OSError(f"{BLOCKED}: {address[0]}")
        original_connect(self, address)

    def connect_ex(self: socket.socket, address: Any) -> int:
        if not _is_allowed(address, allowed):
            raise OSError(f"{BLOCKED}: {address[0]}")
        return original_connect_ex(self, address)

    socket.socket.connect = connect  # type: ignore[method-assign]
    socket.socket.connect_ex = connect_ex  # type: ignore[method-assign]
    try:
        yield
    finally:
        socket.socket.connect = original_connect  # type: ignore[method-assign]
        socket.socket.connect_ex = original_connect_ex  # type: ignore[method-assign]


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
