from __future__ import annotations

import logging
from collections.abc import Mapping

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse

from riftlab.observability import safe_exception_description
from riftlab.shared.errors import (
    ConflictError,
    DecisionsNotPreparedError,
    InvalidCursorError,
    NotFoundError,
    RiotKeyMissingError,
)

logger = logging.getLogger("riftlab.api")

PROBLEM_MEDIA_TYPE = "application/problem+json"

# Codigos fechados por docs/contracts.md ("Erros: 400 INVALID_MATCH_ID, 404
# RESOURCE_NOT_FOUND, 409 IDEMPOTENCY_CONFLICT/IMPORT_ACTIVE, 422
# VALIDATION_ERROR, 503 DATABASE_UNAVAILABLE"). INTERNAL_ERROR (500) nao e
# citado literalmente no contrato de erros de dominio, mas e o generico de
# infraestrutura ja usado desde o CC-101 para excecao nao tratada.
DATABASE_UNAVAILABLE = "DATABASE_UNAVAILABLE"
DECISIONS_NOT_PREPARED = "DECISIONS_NOT_PREPARED"
INVALID_CURSOR = "INVALID_CURSOR"
IMPORT_ACTIVE = "IMPORT_ACTIVE"
INTERNAL_ERROR = "INTERNAL_ERROR"
RESOURCE_NOT_FOUND = "RESOURCE_NOT_FOUND"
RIOT_KEY_MISSING = "RIOT_KEY_MISSING"
VALIDATION_ERROR = "VALIDATION_ERROR"

# exc.detail de um StarletteHTTPException/HTTPException e texto livre definido
# por quem lanca a excecao (nosso codigo, dependencia ou o proprio Starlette) —
# nunca confiavel para o corpo publico, pois codigo de rota futuro pode
# involuntariamente colocar entrada do cliente ali (revisao CC-106). titulo e
# detalhe vem so deste mapa fechado por status_code; status nao mapeado cai no
# fallback generico, nunca ecoando exc.detail.
_KNOWN_HTTP_PROBLEM_TEXT: dict[int, tuple[str, str]] = {
    400: ("Requisicao invalida", "A requisicao nao pode ser processada."),
    404: ("Recurso nao encontrado", "Recurso ou rota nao encontrados."),
    405: ("Metodo nao permitido", "Metodo HTTP nao suportado para esta rota."),
    409: ("Conflito", "A requisicao conflita com o estado atual do recurso."),
}
_DEFAULT_HTTP_PROBLEM_TITLE = "Erro na requisicao"
_DEFAULT_HTTP_PROBLEM_DETAIL = "A requisicao nao pode ser atendida."

# error["type"] do pydantic cujo loc pode conter um segmento escolhido pelo
# cliente (nao um nome de campo do nosso schema): extra_forbidden ecoa a
# propria chave desconhecida enviada no payload em loc[-1]. Nunca logar esse
# segmento textualmente (revisao CC-106); os demais tipos de erro atuais do
# projeto so tem nomes de campo do proprio schema em loc (nenhum schema em
# shared/api/ tem dict[str, <tipo estrito>] hoje — so dict[str, Any], que nunca
# falha por chave). Se um schema futuro adicionar um dict de chave estrita,
# revisar este conjunto.
_CLIENT_CONTROLLED_LOC_ERROR_TYPES = frozenset({"extra_forbidden"})
_REDACTED_LOC_SEGMENT = "<campo-desconhecido>"


class ProblemDetail(BaseModel):
    type: str
    title: str
    status: int
    code: str
    detail: str
    requestId: str


def problem_response(
    request: Request,
    *,
    status_code: int,
    code: str,
    title: str,
    detail: str,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    """Monta erro application/problem+json sem DSN, SQL, stack trace ou segredo,
    conforme docs/contracts.md. headers e so para cabecalhos de protocolo
    conhecidos (ex.: Allow em 405) que o proprio Starlette anexa a
    HTTPException — nunca texto controlado pelo cliente (revisao CC-106)."""
    request_id = getattr(request.state, "request_id", "-")
    problem = ProblemDetail(
        type=f"https://riftlab.local/problems/{code.lower()}",
        title=title,
        status=status_code,
        code=code,
        detail=detail,
        requestId=request_id,
    )
    return JSONResponse(
        status_code=status_code,
        content=problem.model_dump(),
        media_type=PROBLEM_MEDIA_TYPE,
        headers=dict(headers) if headers else None,
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Registra os tres handlers padrao de erro em application/problem+json
    (docs/contracts.md): validacao (422), HTTPException/rota inexistente
    (404 e demais) e falha nao tratada (500). Extraido de
    riftlab.api.app.create_app para que testes de contrato (CC-106) possam
    exercitar os tres caminhos numa app FastAPI so de teste, sem criar rota
    publica na aplicacao real so para provocar falha."""

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # exc.errors() inclui o valor bruto submetido ("input"); nunca repassar
        # ao cliente nem logar campo a campo com esse valor. loc tambem nao e
        # sempre seguro para ecoar: em extra_forbidden, loc[-1] e a propria
        # chave desconhecida enviada pelo cliente, nao um nome do nosso schema
        # (revisao CC-106) — redigido antes de logar.
        field_locations = []
        for error in exc.errors():
            parts = [str(part) for part in error["loc"] if part not in ("body", "query", "path")]
            if error["type"] in _CLIENT_CONTROLLED_LOC_ERROR_TYPES and parts:
                parts[-1] = _REDACTED_LOC_SEGMENT
            field_locations.append(".".join(parts))
        logger.warning(
            "validacao de requisicao falhou campos=%s tipos=%s",
            field_locations,
            [error["type"] for error in exc.errors()],
        )
        count = len(exc.errors())
        plural = "campo" if count == 1 else "campos"
        return problem_response(
            request,
            status_code=422,
            code=VALIDATION_ERROR,
            title="Requisicao invalida",
            detail=f"{count} {plural} da requisicao nao passaram na validacao.",
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        # exc.detail nunca e repassado ao cliente: e texto livre de quem lanca
        # a excecao (rota futura pode colocar entrada do usuario ali sem
        # querer) — so titulo/detalhe deste mapa fechado por status_code, com
        # fallback generico (revisao CC-106).
        code = RESOURCE_NOT_FOUND if exc.status_code == 404 else f"HTTP_{exc.status_code}"
        title, detail = _KNOWN_HTTP_PROBLEM_TEXT.get(
            exc.status_code, (_DEFAULT_HTTP_PROBLEM_TITLE, _DEFAULT_HTTP_PROBLEM_DETAIL)
        )
        return problem_response(
            request,
            status_code=exc.status_code,
            code=code,
            title=title,
            detail=detail,
            headers=exc.headers,
        )

    @app.exception_handler(NotFoundError)
    async def not_found_error_handler(request: Request, exc: NotFoundError) -> JSONResponse:
        # NotFoundError e generico de dominio (agregado solicitado nao existe,
        # shared/errors.py) — mensagem interna de exc nunca repassada, mesmo
        # racional do StarletteHTTPException (revisao CC-106).
        return problem_response(
            request,
            status_code=404,
            code=RESOURCE_NOT_FOUND,
            title="Recurso nao encontrado",
            detail="Recurso ou rota nao encontrados.",
        )

    @app.exception_handler(ConflictError)
    async def conflict_error_handler(request: Request, exc: ConflictError) -> JSONResponse:
        # Unico produtor atual de ConflictError ate a borda HTTP e o retry de
        # import (CC-201: job alvo nao esta FAILED) — corrida na criacao do
        # job ativo e absorvida antes, dentro de ingestion.application, nunca
        # chega aqui. Se um cenario de conflito com outro codigo (ex.:
        # IDEMPOTENCY_CONFLICT) precisar de um 409 diferente no futuro, este
        # handler generico deixa de bastar — revisar entao.
        specific = str(exc) if str(exc) in {"IDEMPOTENCY_CONFLICT", "ATTEMPT_LOCKED"} else None
        return problem_response(
            request,
            status_code=409,
            code=specific or IMPORT_ACTIVE,
            title="Conflito",
            detail="A requisicao conflita com o estado atual do recurso.",
        )

    @app.exception_handler(DecisionsNotPreparedError)
    async def decisions_not_prepared_handler(
        request: Request, exc: DecisionsNotPreparedError
    ) -> JSONResponse:
        # CC-204: a partida existe, mas as decisoes da versao corrente ainda nao
        # foram extraidas — distinto de 200 com items=[] ("extraida com zero
        # pontos"). Texto fixo: nunca repassa mensagem de exc.
        return problem_response(
            request,
            status_code=409,
            code=DECISIONS_NOT_PREPARED,
            title="Decisoes nao preparadas",
            detail="As decisoes desta partida ainda nao foram extraidas para a versao corrente.",
        )

    @app.exception_handler(InvalidCursorError)
    async def invalid_cursor_handler(request: Request, exc: InvalidCursorError) -> JSONResponse:
        # Nunca ecoa o cursor recebido (nem parte dele).
        return problem_response(
            request,
            status_code=422,
            code=INVALID_CURSOR,
            title="Cursor invalido",
            detail="O cursor de paginacao e invalido para esta consulta.",
        )

    @app.exception_handler(RiotKeyMissingError)
    async def riot_key_missing_handler(request: Request, exc: RiotKeyMissingError) -> JSONResponse:
        # CC-205: 422 com código próprio (antes era o HTTP_422 genérico), para o
        # cliente mostrar a orientação certa. Texto fixo: nunca menciona chave,
        # variável de ambiente nem caminho.
        return problem_response(
            request,
            status_code=422,
            code=RIOT_KEY_MISSING,
            title="Modo RIOT indisponivel",
            detail="A importacao no modo RIOT exige uma chave da Riot configurada no servidor.",
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        # logger.exception()/exc_info=True anexaria a excecao original inteira
        # ao LogRecord, visivel a qualquer handler que a formate (nao so o
        # nosso _JsonLogFormatter) — descricao ja sanitizada antes de logar
        # (revisao CC-106; ver riftlab.observability.safe_exception_description).
        logger.error("erro nao tratado detalhe=%s", safe_exception_description(exc))
        return problem_response(
            request,
            status_code=500,
            code=INTERNAL_ERROR,
            title="Erro interno",
            detail="Falha inesperada ao processar a requisicao.",
        )
