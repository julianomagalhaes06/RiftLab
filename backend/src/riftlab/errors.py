from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger("riftlab.api")

PROBLEM_MEDIA_TYPE = "application/problem+json"

# Codigos fechados por docs/contracts.md ("Erros: 400 INVALID_MATCH_ID, 404
# RESOURCE_NOT_FOUND, 409 IDEMPOTENCY_CONFLICT/IMPORT_ACTIVE, 422
# VALIDATION_ERROR, 503 DATABASE_UNAVAILABLE"). INTERNAL_ERROR (500) nao e
# citado literalmente no contrato de erros de dominio, mas e o generico de
# infraestrutura ja usado desde o CC-101 para excecao nao tratada.
DATABASE_UNAVAILABLE = "DATABASE_UNAVAILABLE"
INTERNAL_ERROR = "INTERNAL_ERROR"
RESOURCE_NOT_FOUND = "RESOURCE_NOT_FOUND"
VALIDATION_ERROR = "VALIDATION_ERROR"


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
) -> JSONResponse:
    """Monta erro application/problem+json sem DSN, SQL, stack trace ou segredo,
    conforme docs/contracts.md."""
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
        # ao cliente nem logar campo a campo com esse valor — so o local (loc)
        # e o tipo do erro (codigo estavel do pydantic) sao seguros.
        field_locations = [
            ".".join(str(part) for part in error["loc"] if part not in ("body", "query", "path"))
            for error in exc.errors()
        ]
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
        code = RESOURCE_NOT_FOUND if exc.status_code == 404 else f"HTTP_{exc.status_code}"
        detail = (
            exc.detail
            if isinstance(exc.detail, str) and exc.detail
            else "Recurso ou rota nao encontrados."
        )
        return problem_response(
            request,
            status_code=exc.status_code,
            code=code,
            title="Erro na requisicao",
            detail=detail,
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("erro nao tratado")
        return problem_response(
            request,
            status_code=500,
            code=INTERNAL_ERROR,
            title="Erro interno",
            detail="Falha inesperada ao processar a requisicao.",
        )
