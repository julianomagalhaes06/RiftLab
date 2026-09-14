from __future__ import annotations

import asyncio
import logging
import signal
from pathlib import Path

from riftlab.config import Settings, get_settings
from riftlab.observability import configure_logging

logger = logging.getLogger("riftlab.worker")


def _touch_heartbeat(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(exist_ok=True)


async def run(settings: Settings | None = None) -> None:
    """Processo minimo e verificavel por heartbeat em arquivo; nao processa jobs
    ainda (fila/import pertencem a CC-103/201). Encerra de forma limpa em
    SIGTERM/SIGINT, removendo o heartbeat."""
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    heartbeat_path = Path(settings.worker_heartbeat_path)

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop_event.set)

    logger.info("riftlab worker iniciado heartbeat=%s", heartbeat_path)
    _touch_heartbeat(heartbeat_path)
    try:
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(
                    stop_event.wait(), timeout=settings.worker_heartbeat_interval_seconds
                )
            except TimeoutError:
                _touch_heartbeat(heartbeat_path)
    finally:
        heartbeat_path.unlink(missing_ok=True)
        logger.info("riftlab worker encerrado")


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
