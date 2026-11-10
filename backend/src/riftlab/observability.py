from __future__ import annotations

import json
import logging
import re
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
    """arquivo:linha:funcao de cada quadro — nunca frame.line (texto-fonte da
    linha) nem locals: um literal sensivel escrito direto na linha do raise
    (ex.: raise RuntimeError("segredo-literal")) aparece verbatim no
    texto-fonte, diferente de uma variavel (so o nome aparece) — revisao
    complementar CC-106. Nunca str(exc)/args tambem, que pode carregar
    segredo, DSN, SQL ou entrada bruta do cliente."""
    return [
        f"{frame.filename}:{frame.lineno} em {frame.name}" for frame in traceback.extract_tb(exc_tb)
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


_HTTP_CLIENT_LOGGERS = ("httpx", "httpcore")


def quiet_http_client_logs() -> None:
    """Silencia o INFO do cliente HTTP (`httpx`/`httpcore`): ele registra a URL COMPLETA de
    cada requisição, e as URLs da Riot carregam identificadores pessoais (Riot ID em
    Account-V1, PUUID na listagem de partidas). Idempotente; chamado por
    `configure_logging` e por todo transporte da Riot (CC-206: achado do teste de
    varredura de dado pessoal — sem isto uma coleta real gravaria Riot ID e PUUID no log)."""
    for name in _HTTP_CLIENT_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


_SOCIAL_CALLBACK_QUERY = re.compile(r"(/api/v1/auth/social/[a-z]+/callback)\?\S*")


class _SocialCallbackQueryFilter(logging.Filter):
    """O log de acesso do Uvicorn traz a URL inteira; no retorno do login social, ela leva o
    código de autorização e o `state`, que não podem ir para o log."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(
                _SOCIAL_CALLBACK_QUERY.sub(r"\1", arg) if isinstance(arg, str) else arg
                for arg in record.args
            )
        return True


# Instância única: `addFilter` ignora a repetida quando a aplicação é criada de novo.
_SOCIAL_CALLBACK_FILTER = _SocialCallbackQueryFilter()


def configure_logging(level: str) -> None:
    """Logs estruturados em JSON com requestId; nunca inclui payload bruto,
    DSN ou segredo — chamadores devem logar apenas identificadores e codigos."""
    quiet_http_client_logs()
    root = logging.getLogger()
    root.setLevel(level.upper())
    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(_JsonLogFormatter())
    root.handlers = [handler]
    logging.getLogger("uvicorn.access").addFilter(_SOCIAL_CALLBACK_FILTER)


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
