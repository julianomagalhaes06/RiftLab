"""Correcao CC-106 (revisao Codex, 15/09/2026): logger.exception()/exc_info=True
anexava a excecao original inteira ao LogRecord — visivel a qualquer handler
que a formate, inclusive um handler de terceiros anexado direto ao logger com
formatacao padrao (nao so riftlab.observability._JsonLogFormatter), e ao
proprio logger uvicorn.error apos o ServerErrorMiddleware relancar a excecao
para o servidor real registrar. Estes testes cobrem os dois caminhos: a
descricao segura usada por riftlab.errors (sem exc_info bruto) e um servidor
uvicorn real de ponta a ponta, reproduzindo exatamente como "cc api" roda
(log_config=None, uvicorn.error propaga ate o formatter da raiz).

Revisao complementar (mesmo dia, sobre o commit c1c5454): os testes acima
usam um segredo guardado numa variavel (SENTINEL) interpolada por f-string —
o texto-fonte da linha do raise mostra so o nome "SENTINEL", nunca o valor,
entao nao cobriam _safe_frames_from_tb incluindo frame.line (texto-fonte
inteiro do quadro). Um literal sensivel escrito direto na linha do raise
(sem passar por variavel) reaparecia verbatim na descricao "segura". Os
dois testes *_literal_* abaixo escrevem o literal diretamente no raise
(nao via variavel) para reproduzir exatamente esse caminho — nao troque para
uma variavel/f-string, isso esvaziaria a regressao."""

from __future__ import annotations

import asyncio
import io
import logging
import sys

import httpx
import uvicorn
from fastapi import FastAPI

from riftlab.errors import register_exception_handlers
from riftlab.observability import (
    RequestIdMiddleware,
    _JsonLogFormatter,
    configure_logging,
    safe_exception_description,
)

SENTINEL = "CC106_LOGGING_SYNTHETIC_SECRET"  # noqa: S105


def test_safe_exception_description_omits_message_but_keeps_type_and_location() -> None:
    try:
        raise RuntimeError(f"falha com segredo sintetico: {SENTINEL}")
    except RuntimeError as exc:
        description = safe_exception_description(exc)

    assert SENTINEL not in description
    assert "RuntimeError" in description
    assert "test_error_logging.py" in description


LITERAL_SENTINEL = "CC106_LITERAL_SYNTHETIC_SECRET_IN_SOURCE_LINE"  # noqa: S105


def test_safe_exception_description_omits_literal_written_directly_in_raise() -> None:
    """frame.line e o texto-fonte inteiro do raise — um literal (nao uma
    variavel) escrito ali aparece verbatim se _safe_frames_from_tb incluir
    frame.line (achado real da revisao complementar sobre c1c5454)."""
    try:
        raise RuntimeError("CC106_LITERAL_SYNTHETIC_SECRET_IN_SOURCE_LINE")
    except RuntimeError as exc:
        description = safe_exception_description(exc)

    assert LITERAL_SENTINEL not in description
    assert "RuntimeError" in description


def test_json_log_formatter_omits_literal_written_directly_in_raise() -> None:
    """Mesmo achado, caminho do formatter (exc_info de qualquer chamador,
    ex.: uvicorn.error) — nao so o de safe_exception_description."""
    try:
        raise RuntimeError("CC106_LITERAL_SYNTHETIC_SECRET_IN_SOURCE_LINE")
    except RuntimeError:
        record = logging.LogRecord(
            name="terceiro.qualquer",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="erro nao tratado",
            args=(),
            exc_info=sys.exc_info(),
        )

    formatted = _JsonLogFormatter().format(record)
    assert LITERAL_SENTINEL not in formatted
    assert "RuntimeError" in formatted


async def test_unhandled_exception_secret_absent_even_with_default_formatter_handler(
    caplog,
) -> None:
    """Reproduz a metodologia da revisao: um StreamHandler com formatacao
    PADRAO (nao _JsonLogFormatter) anexado direto ao logger riftlab.api. Prova
    que a seguranca nao depende de qual formatter esta configurado, porque
    riftlab.errors nunca anexa exc_info bruto ao LogRecord."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    api_logger = logging.getLogger("riftlab.api")
    api_logger.addHandler(handler)

    app = FastAPI()
    app.add_middleware(RequestIdMiddleware)
    register_exception_handlers(app)

    @app.get("/__test__/boom")
    async def _boom() -> None:
        raise RuntimeError(f"falha interna com segredo sintetico: {SENTINEL}")

    try:
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
            response = await client.get("/__test__/boom")
    finally:
        api_logger.removeHandler(handler)

    assert response.status_code == 500
    log_output = stream.getvalue()
    assert SENTINEL not in log_output
    assert "RuntimeError" in log_output  # correlacao ainda presente, so a mensagem e sanitizada


def test_json_log_formatter_sanitizes_exc_info_from_any_caller() -> None:
    """Defesa em profundidade: mesmo um chamador que ainda anexe exc_info bruto
    (ex.: uma biblioteca de terceiros, ou o proprio uvicorn.error) tem a
    mensagem sanitizada pelo formatter da raiz."""
    try:
        raise RuntimeError(f"falha com segredo sintetico: {SENTINEL}")
    except RuntimeError:
        record = logging.LogRecord(
            name="terceiro.qualquer",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="erro nao tratado",
            args=(),
            exc_info=sys.exc_info(),
        )

    formatted = _JsonLogFormatter().format(record)
    assert SENTINEL not in formatted
    assert "RuntimeError" in formatted


async def test_real_uvicorn_server_never_logs_secret_to_stdout() -> None:
    """Caminho efetivo do servidor (revisao CC-106): um uvicorn.Server real,
    com log_config=None — a mesma configuracao de "cc api" (riftlab.cli.main) —
    atendendo uma requisicao real por socket TCP. Starlette's ServerErrorMiddleware
    relanca a excecao original depois do nosso handler responder; sem
    log_config=None o uvicorn.error logaria com o formatter padrao dele, e com
    log_config=None ele propaga para a raiz e passa pelo nosso
    _JsonLogFormatter — os dois caminhos (nosso handler e uvicorn.error) tem
    que ficar sem o segredo no stdout capturado."""
    app = FastAPI()
    app.add_middleware(RequestIdMiddleware)
    register_exception_handlers(app)

    @app.get("/__test__/boom")
    async def _boom() -> None:
        raise RuntimeError(f"falha interna com segredo sintetico: {SENTINEL}")

    configure_logging("INFO")
    root = logging.getLogger()
    previous_handlers = list(root.handlers)
    captured = io.StringIO()
    for handler in root.handlers:
        handler.stream = captured

    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_config=None, lifespan="off")
    server = uvicorn.Server(config)
    serve_task = asyncio.create_task(server.serve())
    try:
        for _ in range(500):
            if server.started:
                break
            await asyncio.sleep(0.01)
        else:
            raise AssertionError("uvicorn nao iniciou a tempo")

        port = server.servers[0].sockets[0].getsockname()[1]
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            response = await client.get("/__test__/boom")
    finally:
        server.should_exit = True
        await serve_task
        root.handlers = previous_handlers

    assert response.status_code == 500
    assert SENTINEL not in response.text

    log_output = captured.getvalue()
    assert SENTINEL not in log_output
    assert "RuntimeError" in log_output  # correlacao (tipo/quadros) preservada
