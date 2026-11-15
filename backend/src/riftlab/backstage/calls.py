"""Registro das chamadas à Riot para os Bastidores.

O transporte (`RiotHttpTransport`) chama `record` depois de cada tentativa HTTP. Sem registro
instalado (testes, linha de comando), `record` não faz nada. A API e o worker instalam um
`RiotCallLog`, que guarda as chamadas num buffer em memória e as grava no banco a cada segundo,
fora do caminho da requisição: uma falha no registro nunca atrapalha a consulta à Riot. Linhas
com mais de um dia são apagadas pelo próprio registro.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from riftlab.backstage.models import RiotApiCallModel
from riftlab.shared.db import unit_of_work

logger = logging.getLogger(__name__)

KEEP = timedelta(days=1)
_PRUNE_EVERY = timedelta(minutes=1)


@dataclass(frozen=True, slots=True)
class RiotCall:
    at: datetime
    kind: str
    region: str
    outcome: str
    status_code: int | None
    duration_ms: int
    attempt: int


_sink: Callable[[RiotCall], None] | None = None


def install(sink: Callable[[RiotCall], None] | None) -> None:
    """Instala (ou remove, com None) o destino das chamadas registradas neste processo."""
    global _sink
    _sink = sink


def record(
    *,
    kind: str,
    region: str,
    outcome: str,
    status_code: int | None,
    duration_ms: float,
    attempt: int,
) -> None:
    sink = _sink
    if sink is None:
        return
    with contextlib.suppress(Exception):
        sink(
            RiotCall(
                at=datetime.now(UTC),
                kind=kind[:24],
                region=region,
                outcome=outcome[:24],
                status_code=status_code,
                duration_ms=max(0, round(duration_ms)),
                attempt=max(1, attempt),
            )
        )


class RiotCallLog:
    """Buffer limitado em memória, gravado no banco por `run` (ou `flush`)."""

    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        process: str,
        *,
        flush_seconds: float = 1.0,
        max_buffer: int = 2000,
    ) -> None:
        self._factory = factory
        self._process = process
        self._flush_seconds = flush_seconds
        self._buffer: deque[RiotCall] = deque(maxlen=max_buffer)
        self._last_prune: datetime | None = None

    def __call__(self, call: RiotCall) -> None:
        self._buffer.append(call)

    async def flush(self, now: datetime | None = None) -> int:
        calls: list[RiotCall] = []
        while self._buffer:
            calls.append(self._buffer.popleft())
        now = now or datetime.now(UTC)
        prune = self._last_prune is None or now - self._last_prune >= _PRUNE_EVERY
        if not calls and not prune:
            return 0
        async with unit_of_work(self._factory) as session:
            session.add_all(
                RiotApiCallModel(
                    at=call.at,
                    process=self._process,
                    kind=call.kind,
                    region=call.region,
                    outcome=call.outcome,
                    status_code=call.status_code,
                    duration_ms=call.duration_ms,
                    attempt=call.attempt,
                )
                for call in calls
            )
            if prune:
                await session.execute(
                    delete(RiotApiCallModel).where(RiotApiCallModel.at < now - KEEP)
                )
        if prune:
            self._last_prune = now
        return len(calls)

    async def run(self, stop_event: asyncio.Event) -> None:
        """Grava a cada `flush_seconds` até `stop_event`; grava o que sobrou ao sair."""
        while not stop_event.is_set():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop_event.wait(), timeout=self._flush_seconds)
            try:
                await self.flush()
            except Exception:  # banco fora: as chamadas deste lote se perdem, a consulta não
                logger.warning("registro das chamadas à Riot indisponível", exc_info=True)
        with contextlib.suppress(Exception):
            await self.flush()
