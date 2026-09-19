"""Loop principal do worker (CC-201): uma exceção inesperada de
process_next_job não pode derrubar o processo nem escapar para o traceback
bruto do Python (achado real do smoke de compose — sem isto, o stderr do
container mostra SQL/stack sem passar pelo _JsonLogFormatter/
safe_exception_description sanitizados do CC-106). Sem Postgres real: session
engine é construída mas nunca usada de fato (process_next_job é substituído)."""

from __future__ import annotations

import asyncio

from riftlab.config import Settings
from riftlab.worker import main as worker_main

SENTINEL = "CC201_WORKER_LOOP_SYNTHETIC_SECRET"  # noqa: S105


async def test_worker_loop_survives_unexpected_exception_and_logs_safely(
    tmp_path, monkeypatch
) -> None:
    """Substitui worker_main.logger.error diretamente (não um handler de
    logging) — a suíte completa dentro do container de teste mostrou o
    pipeline de logging (handler/stdout/caplog) sensível a estado global de
    outros testes de um jeito que não se reproduz isoladamente nem no host;
    chamar o atributo da instância do logger direto contorna toda essa
    máquina, testando exatamente o que importa: a mensagem passada para
    logger.error nunca contém o segredo, sempre o tipo da exceção real."""
    logged_calls: list[tuple[str, tuple[object, ...]]] = []
    monkeypatch.setattr(
        worker_main.logger, "error", lambda msg, *args: logged_calls.append((msg, args))
    )

    stop_event = asyncio.Event()
    calls = {"n": 0}

    async def fake_process_next_job(session_factory, raw_store, settings) -> bool:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError(f"falha sintetica com segredo: {SENTINEL}")
        stop_event.set()
        return False

    async def fake_process_next_preparation(session_factory, raw_store, settings) -> bool:
        return False  # o loop agora também consulta a preparação (CC-205)

    monkeypatch.setattr(worker_main, "process_next_job", fake_process_next_job)
    monkeypatch.setattr(worker_main, "process_next_preparation", fake_process_next_preparation)

    settings = Settings(
        raw_storage_dir=str(tmp_path / "raw"),
        worker_heartbeat_path=str(tmp_path / "heartbeat"),
        worker_poll_interval_seconds=0.01,
    )

    await asyncio.wait_for(worker_main.run(settings, stop_event=stop_event), timeout=5)

    assert calls["n"] >= 2  # sobreviveu à primeira falha e continuou o loop
    assert len(logged_calls) == 1
    msg, args = logged_calls[0]
    full_text = (msg % args) if args else msg
    assert SENTINEL not in full_text
    assert "RuntimeError" in full_text
    assert "ciclo do worker falhou de forma inesperada" in msg


async def test_worker_loop_runs_preparation_each_cycle_and_survives_its_failure(
    tmp_path, monkeypatch
) -> None:
    """CC-205: cada ciclo processa importação E preparação; uma exceção inesperada
    da preparação também não derruba o processo e é logada sem o segredo."""
    logged_calls: list[tuple[str, tuple[object, ...]]] = []
    monkeypatch.setattr(
        worker_main.logger, "error", lambda msg, *args: logged_calls.append((msg, args))
    )
    stop_event = asyncio.Event()
    calls = {"imports": 0, "preparations": 0}

    async def fake_process_next_job(session_factory, raw_store, settings) -> bool:
        calls["imports"] += 1
        return False

    async def fake_process_next_preparation(session_factory, raw_store, settings) -> bool:
        calls["preparations"] += 1
        if calls["preparations"] == 1:
            raise RuntimeError(f"falha sintetica com segredo: {SENTINEL}")
        stop_event.set()
        return False

    monkeypatch.setattr(worker_main, "process_next_job", fake_process_next_job)
    monkeypatch.setattr(worker_main, "process_next_preparation", fake_process_next_preparation)
    settings = Settings(
        raw_storage_dir=str(tmp_path / "raw"),
        worker_heartbeat_path=str(tmp_path / "heartbeat"),
        worker_poll_interval_seconds=0.01,
    )

    await asyncio.wait_for(worker_main.run(settings, stop_event=stop_event), timeout=5)

    assert calls["preparations"] >= 2 and calls["imports"] >= 1
    assert len(logged_calls) == 1
    msg, args = logged_calls[0]
    full_text = (msg % args) if args else msg
    assert SENTINEL not in full_text and "RuntimeError" in full_text


async def test_recurring_import_failure_never_starves_preparation(tmp_path, monkeypatch) -> None:
    """CC-205 (N1): uma exceção inesperada RECORRENTE do lado de importação não pode
    pular a preparação em nenhum ciclo — cada passo tem seu próprio tratamento, e
    cada falha é logada (sanitizada) com a etapa que a produziu."""
    logged_calls: list[tuple[str, tuple[object, ...]]] = []
    monkeypatch.setattr(
        worker_main.logger, "error", lambda msg, *args: logged_calls.append((msg, args))
    )
    stop_event = asyncio.Event()
    calls = {"imports": 0, "preparations": 0}
    prepared_ids: list[int] = []

    async def always_failing_import(session_factory, raw_store, settings) -> bool:
        calls["imports"] += 1
        raise RuntimeError(f"falha recorrente com segredo: {SENTINEL}")

    async def working_preparation(session_factory, raw_store, settings) -> bool:
        calls["preparations"] += 1
        prepared_ids.append(calls["preparations"])
        if calls["preparations"] >= 4:
            stop_event.set()
            return False
        return True  # há trabalho: o próximo ciclo não espera

    monkeypatch.setattr(worker_main, "process_next_job", always_failing_import)
    monkeypatch.setattr(worker_main, "process_next_preparation", working_preparation)
    settings = Settings(
        raw_storage_dir=str(tmp_path / "raw"),
        worker_heartbeat_path=str(tmp_path / "heartbeat"),
        worker_poll_interval_seconds=0.01,
    )

    await asyncio.wait_for(worker_main.run(settings, stop_event=stop_event), timeout=5)

    assert calls["preparations"] == 4  # atendida em TODOS os ciclos apesar do import quebrado
    assert calls["imports"] == calls["preparations"]  # e o import continua sendo tentado
    assert len(logged_calls) == calls["imports"]  # uma falha logada por ciclo
    for msg, args in logged_calls:
        full_text = msg % args
        assert SENTINEL not in full_text and "RuntimeError" in full_text
        assert "etapa=importacao" in full_text
