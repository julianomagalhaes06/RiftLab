from __future__ import annotations

import json
import logging
import sys
import traceback
import uuid
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from types import TracebackType

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

_request_id_ctx: ContextVar[str] = ContextVar("request_id", default="-")


def current_request_id() -> str:
    return _request_id_ctx.get()


def _safe_frames_from_tb(exc_tb: TracebackType | None) -> list[str]:
    """arquivo:linha:funcao de cada quadro (traceback.extract_tb: so o
    texto-fonte da linha, nunca valor em runtime) — nunca str(exc)/args, que
    pode carregar segredo, DSN, SQL ou entrada bruta do cliente."""
    return [
        f"{frame.filename}:{frame.lineno} em {frame.name}: {frame.line}"
        for frame in traceback.extract_tb(exc_tb)
    ]


def safe_exception_description(exc: BaseException) -> str:
    """Descricao segura de uma excecao para log de aplicacao: tipo + quadros,
    nunca str(exc)/args (revisao CC-106). Preferir isto a logger.exception()/
    exc_info=True em qualquer logger cujos handlers nao sejam garantidamente
    _JsonLogFormatter — exc_info carrega a excecao original inteira no
    LogRecord, visivel a QUALQUER handler que a formate (inclusive um anexado
    diretamente ao logger com formatacao padrao, fora do nosso controle);
    montar a string segura antes de logar remove a excecao do LogRecord desde
    a origem, nao so no formatter."""
    type_name = type(exc).__name__
    frames = _safe_frames_from_tb(exc.__traceback__)
    return " | ".join([f"tipo={type_name}", *frames])


class _JsonLogFormatter(logging.Formatter):
    """Formatter da raiz (configure_logging): sanitiza exc_info de QUALQUER
    chamador que ainda o anexe ao LogRecord (ex.: bibliotecas de terceiros e o
    proprio logger uvicorn.error, que Uvicorn usa para registrar a excecao
    apos o ServerErrorMiddleware rela-la; com log_config=None esse logger
    propaga ate a raiz e passa por este formatter). Defesa em profundidade:
    riftlab.errors evita exc_info=True nos seus proprios logs (ver
    safe_exception_description), entao isto cobre codigo que este projeto nao
    controla, nao o caminho principal."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, str | list[str]] = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "requestId": current_request_id(),
        }
        if record.exc_info:
            exc_type, _exc_value, exc_tb = record.exc_info
            type_name = exc_type.__name__ if exc_type is not None else "Exception"
            payload["excFrames"] = [f"tipo={type_name}", *_safe_frames_from_tb(exc_tb)]
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level: str) -> None:
    """Logs estruturados em JSON com requestId; nunca inclui payload bruto,
    DSN ou segredo — chamadores devem logar apenas identificadores e codigos."""
    root = logging.getLogger()
    root.setLevel(level.upper())
    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(_JsonLogFormatter())
    root.handlers = [handler]


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        incoming = request.headers.get("x-request-id")
        request_id = incoming if incoming else str(uuid.uuid4())
        token = _request_id_ctx.set(request_id)
        try:
            request.state.request_id = request_id
            response = await call_next(request)
        finally:
            _request_id_ctx.reset(token)
        response.headers["X-Request-Id"] = request_id
        return response
