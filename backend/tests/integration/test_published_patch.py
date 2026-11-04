"""Patch atual do jogo (linha `published`), separado da janela de análise (`current`)."""

from __future__ import annotations

import pytest
from sqlalchemy import delete, select

from riftlab.catalog.patches import (
    PUBLISHED_RETRY_SECONDS,
    CurrentPatchModel,
    PublishedPatchRefresher,
    current_patch_window,
    published_patch,
    record_current_patch,
    record_published_patch,
)
from riftlab.config import Settings
from riftlab.shared.db import read_only_unit_of_work, unit_of_work
from riftlab.shared.errors import SourceError


@pytest.fixture
async def saved_rows(session_factory):
    """Preserva as linhas existentes e as devolve no fim (a DB é compartilhada)."""
    async with session_factory() as session:
        rows = [
            {
                column.name: getattr(row, column.name)
                for column in CurrentPatchModel.__table__.columns
            }
            for row in (await session.scalars(select(CurrentPatchModel))).all()
        ]
    async with unit_of_work(session_factory) as session:
        await session.execute(delete(CurrentPatchModel))
    yield
    async with unit_of_work(session_factory) as session:
        await session.execute(delete(CurrentPatchModel))
        session.add_all(CurrentPatchModel(**row) for row in rows)


class FakeRaw:
    def __init__(self, body: bytes) -> None:
        self.body = body


class FakeTransport:
    def __init__(self, body: bytes | None = None, *, fail: bool = False) -> None:
        self.body, self.fail, self.calls, self.closed = body, fail, 0, False

    async def fetch_versions(self) -> FakeRaw:
        self.calls += 1
        if self.fail:
            raise SourceError(code="DATA_DRAGON_UNAVAILABLE", detail="sintético")
        assert self.body is not None
        return FakeRaw(self.body)

    async def aclose(self) -> None:
        self.closed = True


async def _state(session_factory) -> tuple[str | None, tuple[str, ...] | None]:
    async with read_only_unit_of_work(session_factory) as session:
        return await published_patch(session), await current_patch_window(session)


@pytest.mark.asyncio
async def test_published_patch_never_moves_the_analysis_window(session_factory, saved_rows):
    await record_current_patch(session_factory, b'["16.19.1","16.18.1"]')
    assert await _state(session_factory) == ("16.19", ("16.19", "16.18"))
    await record_published_patch(session_factory, b'["16.22.1","16.21.1","16.20.1"]')
    assert await _state(session_factory) == ("16.22", ("16.19", "16.18"))
    # Detectar de novo move a janela e também o publicado.
    await record_current_patch(session_factory, b'["16.23.1","16.22.1"]')
    assert await _state(session_factory) == ("16.23", ("16.23", "16.22"))


@pytest.mark.asyncio
async def test_refresher_only_uses_the_network_in_real_mode_and_respects_intervals(
    session_factory, saved_rows
):
    now = [1000.0]
    transports: list[FakeTransport] = []

    def factory(transport: FakeTransport):
        def make() -> FakeTransport:
            transports.append(transport)
            return transport

        return make

    demo = PublishedPatchRefresher(
        session_factory,
        Settings(riot_api_key=None),
        transport_factory=factory(FakeTransport(b'["16.22.1"]')),
        clock=lambda: now[0],
    )
    assert await demo.step() is False
    assert transports == []  # demonstração: nenhuma rede

    settings = Settings(riot_api_key="k", published_patch_refresh_seconds=3600)
    failing = FakeTransport(fail=True)
    refresher = PublishedPatchRefresher(
        session_factory, settings, transport_factory=factory(failing), clock=lambda: now[0]
    )
    assert await refresher.step() is False
    assert failing.calls == 1 and failing.closed
    assert (await _state(session_factory))[0] is None
    now[0] += PUBLISHED_RETRY_SECONDS - 1
    await refresher.step()
    assert failing.calls == 1  # espera antes de tentar de novo

    working = FakeTransport(b'["16.22.1","16.21.1"]')
    refresher._transport_factory = factory(working)
    now[0] += 1
    await refresher.step()
    assert working.calls == 1 and (await _state(session_factory))[0] == "16.22"
    now[0] += 3599
    await refresher.step()
    assert working.calls == 1  # dentro do intervalo de renovação
    now[0] += 1
    await refresher.step()
    assert working.calls == 2
