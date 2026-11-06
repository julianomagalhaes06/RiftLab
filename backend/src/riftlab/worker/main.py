from __future__ import annotations

import asyncio
import logging
import signal
from collections.abc import Awaitable, Callable
from pathlib import Path

from riftlab import db
from riftlab.catalog.patches import PublishedPatchRefresher
from riftlab.config import Settings, get_settings
from riftlab.history_sync.application import process_next_history_sync
from riftlab.ingestion.application import process_next_job
from riftlab.ingestion.infrastructure.raw_store import DurableRawMatchStore
from riftlab.ingestion.purge import process_next_purge
from riftlab.observability import configure_logging, safe_exception_description
from riftlab.preparation.application import process_next_preparation
from riftlab.shared.db import build_session_factory

logger = logging.getLogger("riftlab.worker")


def _touch_heartbeat(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(exist_ok=True)


async def _run_step(step: str, call: Callable[[], Awaitable[bool]]) -> bool:
    """Executa UM passo do ciclo (importação ou preparação) com tratamento
    próprio: uma exceção inesperada recorrente de um lado não pode impedir o
    outro de ser atendido em todo ciclo (CC-205, nota N1). Erros JÁ classificados
    (SourceError e afins) nunca chegam aqui — são tratados dentro de cada
    process_next_*. Devolve False (nada processado) depois de logar sanitizado.

    Achado real (smoke CC-201): sem isto, uma exceção inesperada (ex.: erro de
    conectividade, bug não previsto) escapa até o sys.excepthook padrão do
    Python, que imprime o traceback BRUTO no stderr do container — sem passar
    pelo _JsonLogFormatter/safe_exception_description sanitizados do CC-106, e
    derrubando o processo inteiro por uma falha que o próximo ciclo poderia
    superar sozinho (ex.: banco momentaneamente indisponível)."""
    try:
        return await call()
    except Exception as exc:
        logger.error(
            "ciclo do worker falhou de forma inesperada etapa=%s detalhe=%s",
            step,
            safe_exception_description(exc),
        )
        return False


async def _idle(stop_event: asyncio.Event, seconds: float) -> None:
    """Espera o intervalo de ociosidade, acordando na hora se o worker for parado."""
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=seconds)
    except TimeoutError:
        pass


async def run(settings: Settings | None = None, stop_event: asyncio.Event | None = None) -> None:
    """Processo verificável por heartbeat em arquivo. O trabalho corre em trilhas
    independentes, cada uma com seu tratamento de falha:

    - importação: `worker_import_concurrency` trilhas em paralelo (CC-201: reserva ->
      busca fora de transação -> valida -> persiste). A reserva por lease com
      `SKIP LOCKED` garante um dono por job, e as chamadas à Riot dividem um limitador
      no processo; como cada importação espera a rede quase o tempo todo, 20 partidas
      chegam em segundos, não uma a uma;
    - preparação (CC-205: catálogo -> reconstrução -> extração): uma trilha, sem esperar
      a importação seguinte nem ser esperada por ela;
    - manutenção: expurgo (CC-603), sincronização de histórico e patch publicado.

    Trilha com trabalho segue sem esperar; ociosa, espera `worker_poll_interval_seconds`.
    Encerra de forma limpa em SIGTERM/SIGINT, removendo o heartbeat — cada trilha termina
    o passo atual antes de sair; um crash abrupto (kill -9, OOM) é coberto pelo lease do
    job. stop_event é injetável para teste determinístico, sem depender de sinal do SO."""
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    heartbeat_path = Path(settings.worker_heartbeat_path)

    stop_event = stop_event if stop_event is not None else asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop_event.set)

    logger.info(
        "riftlab worker iniciado heartbeat=%s importacoes_simultaneas=%d",
        heartbeat_path,
        settings.worker_import_concurrency,
    )
    _touch_heartbeat(heartbeat_path)

    engine = db.build_engine(settings.database_url)
    session_factory = build_session_factory(engine)
    raw_store = DurableRawMatchStore(Path(settings.raw_storage_dir))
    published = PublishedPatchRefresher(session_factory, settings)

    async def lane(steps: tuple[tuple[str, Callable[[], Awaitable[bool]]], ...]) -> None:
        while not stop_event.is_set():
            processed = False
            for step, call in steps:
                processed = await _run_step(step, call) or processed
            _touch_heartbeat(heartbeat_path)
            if processed:
                await asyncio.sleep(0)  # vez das outras trilhas antes do próximo job
            else:
                await _idle(stop_event, settings.worker_poll_interval_seconds)

    async def patch_step() -> bool:
        await published.step()
        return False  # patch atual do jogo para as telas; nunca conta como trabalho de fila

    importing = (("importacao", lambda: process_next_job(session_factory, raw_store, settings)),)
    preparing = (
        ("preparacao", lambda: process_next_preparation(session_factory, raw_store, settings)),
    )
    maintenance = (
        ("expurgo", lambda: process_next_purge(session_factory, raw_store, settings)),
        ("sincronizacao", lambda: process_next_history_sync(session_factory, settings)),
        ("patch publicado", patch_step),
    )
    try:
        await asyncio.gather(
            *(lane(importing) for _ in range(settings.worker_import_concurrency)),
            lane(preparing),
            lane(maintenance),
        )
    finally:
        await engine.dispose()
        heartbeat_path.unlink(missing_ok=True)
        logger.info("riftlab worker encerrado")


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
