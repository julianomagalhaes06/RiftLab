from __future__ import annotations

import asyncio
import logging
import signal
from pathlib import Path

from riftlab import db
from riftlab.config import Settings, get_settings
from riftlab.ingestion.application import process_next_job
from riftlab.ingestion.infrastructure.raw_store import DurableRawMatchStore
from riftlab.observability import configure_logging, safe_exception_description
from riftlab.shared.db import build_session_factory

logger = logging.getLogger("riftlab.worker")


def _touch_heartbeat(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(exist_ok=True)


async def run(settings: Settings | None = None, stop_event: asyncio.Event | None = None) -> None:
    """Processo verificável por heartbeat em arquivo, processando import_jobs
    (CC-201): reserva -> busca (fora de transação) -> valida -> persiste um
    job por ciclo, sem esperar quando há fila (poll_interval só quando
    ocioso). Encerra de forma limpa em SIGTERM/SIGINT, removendo o heartbeat
    — um job em andamento no momento do sinal termina seu ciclo atual antes
    de sair; um crash abrupto de verdade (kill -9, OOM) é coberto pelo
    lease/heartbeat de posse do job, não por este sinal. stop_event é
    injetável para teste determinístico do loop sem depender de entrega de
    sinal do SO (timing observado como variável em container)."""
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    heartbeat_path = Path(settings.worker_heartbeat_path)

    stop_event = stop_event if stop_event is not None else asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop_event.set)

    logger.info("riftlab worker iniciado heartbeat=%s", heartbeat_path)
    _touch_heartbeat(heartbeat_path)

    engine = db.build_engine(settings.database_url)
    session_factory = build_session_factory(engine)
    raw_store = DurableRawMatchStore(Path(settings.raw_storage_dir))

    try:
        while not stop_event.is_set():
            try:
                processed = await process_next_job(session_factory, raw_store, settings)
            except Exception as exc:
                # Achado real (smoke CC-201): sem isto, uma excecao inesperada
                # (ex.: erro de conectividade, bug nao previsto) escapa ate o
                # sys.excepthook padrao do Python, que imprime o traceback
                # BRUTO no stderr do container — sem passar pelo
                # _JsonLogFormatter/safe_exception_description sanitizados do
                # CC-106, e derrubando o processo inteiro por uma falha que o
                # proximo ciclo poderia superar sozinho (ex.: banco
                # momentaneamente indisponivel). Logar sanitizado e continuar
                # o loop; erros JA classificados (SourceError e afins) nunca
                # chegam aqui — sao tratados dentro de process_next_job.
                logger.error(
                    "ciclo do worker falhou de forma inesperada detalhe=%s",
                    safe_exception_description(exc),
                )
                processed = False
            _touch_heartbeat(heartbeat_path)
            if not processed:
                try:
                    await asyncio.wait_for(
                        stop_event.wait(), timeout=settings.worker_poll_interval_seconds
                    )
                except TimeoutError:
                    pass
    finally:
        await engine.dispose()
        heartbeat_path.unlink(missing_ok=True)
        logger.info("riftlab worker encerrado")


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
