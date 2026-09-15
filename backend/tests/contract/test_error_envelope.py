"""Contrato de erro (docs/contracts.md): application/problem+json com type, title,
status, code, detail e requestId; sem DSN/SQL/stack trace no corpo.

Os testes de 404/422/500 abaixo usam uma FastAPI() de teste isolada (nao
riftlab.api.app.create_app) que so registra riftlab.errors.register_exception_handlers
e uma rota descartavel — "rota apenas de teste", nunca publica na aplicacao real,
conforme o plano do CC-106 ("nao criar endpoint publico para gerar falhas")."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest_asyncio
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

from riftlab.errors import RESOURCE_NOT_FOUND, VALIDATION_ERROR, register_exception_handlers
from riftlab.observability import RequestIdMiddleware
from riftlab.shared.api.imports import ImportRequest

SENTINEL_SECRET = "sentinela-sql-postgresql+asyncpg://riftlab:s3nh4-nunca-real@db/riftlab"  # noqa: S105
SENTINEL_HTTP_DETAIL = "sentinela-http-exception-detail-nao-confiavel"  # noqa: S105


def _build_error_probe_app() -> FastAPI:
    """App FastAPI so de teste: mesmos handlers de erro da aplicacao real, mais
    rotas que provocam cada caminho de erro (422 corpo invalido, 500 com
    segredo sintetico embutido na mensagem, 400 com HTTPException.detail
    nao confiavel), para provar que nenhum deles vaza no corpo/headers."""
    app = FastAPI()
    app.add_middleware(RequestIdMiddleware)
    register_exception_handlers(app)

    @app.post("/__test__/imports")
    async def _echo_import(payload: ImportRequest) -> dict[str, str]:
        return {"matchId": payload.match_id}

    @app.get("/__test__/boom")
    async def _boom() -> None:
        raise RuntimeError(f"falha interna com segredo sintetico: {SENTINEL_SECRET}")

    @app.get("/__test__/http-error")
    async def _http_error() -> None:
        # simula rota futura que lanca HTTPException com detail nao confiavel
        # (ex.: interpolando um valor recebido do cliente sem querer).
        raise HTTPException(status_code=400, detail=SENTINEL_HTTP_DETAIL)

    return app


@pytest_asyncio.fixture
async def error_probe_client() -> AsyncIterator[AsyncClient]:
    app = _build_error_probe_app()
    # raise_app_exceptions=False: Starlette's ServerErrorMiddleware sempre
    # relanca a excecao original depois de enviar a resposta do handler (para
    # servidores/test clients logarem); sem isso, o teste falharia com o
    # RuntimeError em vez de inspecionar o corpo 500 que o handler devolveu.
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def test_database_unavailable_matches_problem_json_contract(
    app_client_with_unreachable_db,
):
    response = await app_client_with_unreachable_db.get("/health/ready")

    assert response.status_code == 503
    assert response.headers["content-type"].startswith("application/problem+json")

    body = response.json()
    assert set(body.keys()) == {"type", "title", "status", "code", "detail", "requestId"}
    assert body["code"] == "DATABASE_UNAVAILABLE"
    assert body["status"] == 503
    assert body["requestId"] != "-"

    leaked_terms = ("postgresql+asyncpg", "riftlab:riftlab", "Traceback")
    assert not any(term in response.text for term in leaked_terms)


async def test_unknown_route_returns_problem_json_404(app_client_with_unreachable_db):
    """404 real da aplicacao (rota nao mapeada) ja usa o handler generico —
    nao precisa da app de teste."""
    response = await app_client_with_unreachable_db.get("/api/v1/rota-que-nao-existe")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    assert set(body.keys()) == {"type", "title", "status", "code", "detail", "requestId"}
    assert body["code"] == RESOURCE_NOT_FOUND
    assert body["status"] == 404
    assert body["requestId"] != "-"


async def test_invalid_body_returns_problem_json_422_without_echoing_raw_input(
    error_probe_client,
):
    """matchId invalido (nao BR1, nao DEMO) dispara RequestValidationError; o
    corpo do erro nunca ecoa o valor bruto submetido pelo cliente."""
    offending_value = "sentinela-valor-bruto-nao-deve-vazar"
    response = await error_probe_client.post(
        "/__test__/imports", json={"matchId": offending_value, "mode": "RIOT"}
    )

    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    assert set(body.keys()) == {"type", "title", "status", "code", "detail", "requestId"}
    assert body["code"] == VALIDATION_ERROR
    assert body["requestId"] != "-"
    assert offending_value not in response.text


async def test_missing_required_field_returns_problem_json_422(error_probe_client):
    response = await error_probe_client.post("/__test__/imports", json={"mode": "RIOT"})

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == VALIDATION_ERROR


async def test_unknown_field_name_is_redacted_in_validation_log(error_probe_client, caplog):
    """ImportRequest (CamelModel: extra="forbid") rejeita campo desconhecido; o
    erro extra_forbidden do pydantic ecoa a propria chave enviada pelo cliente
    em loc[-1] — nunca a chave literal no log, so um marcador (revisao
    CC-106)."""
    sensitive_field_name = "sentinela-nome-de-campo-nao-deve-vazar-no-log"
    with caplog.at_level("WARNING", logger="riftlab.api"):
        response = await error_probe_client.post(
            "/__test__/imports",
            json={"matchId": "DEMO-0001", "mode": "DEMO", sensitive_field_name: "x"},
        )

    assert response.status_code == 422
    log_text = "\n".join(record.getMessage() for record in caplog.records)
    assert sensitive_field_name not in log_text
    assert "<campo-desconhecido>" in log_text


async def test_unhandled_exception_returns_generic_500_without_leaking_secret(
    error_probe_client,
):
    """Excecao nao tratada com um segredo sintetico embutido na mensagem nunca
    aparece na resposta HTTP — nem no corpo, nem em nenhum header."""
    response = await error_probe_client.get("/__test__/boom")

    assert response.status_code == 500
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    assert set(body.keys()) == {"type", "title", "status", "code", "detail", "requestId"}
    assert body["code"] == "INTERNAL_ERROR"
    assert body["detail"] == "Falha inesperada ao processar a requisicao."

    assert SENTINEL_SECRET not in response.text
    assert "Traceback" not in response.text
    assert "RuntimeError" not in response.text
    for header_value in response.headers.values():
        assert SENTINEL_SECRET not in header_value


async def test_http_exception_detail_is_never_echoed_to_client(error_probe_client):
    """exc.detail e texto livre de quem lanca a excecao — nunca confiavel para
    o corpo publico (revisao CC-106). Titulo/detalhe vem de um mapa fechado
    por status_code, nao do detail arbitrario da excecao."""
    response = await error_probe_client.get("/__test__/http-error")

    assert response.status_code == 400
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    assert set(body.keys()) == {"type", "title", "status", "code", "detail", "requestId"}
    assert SENTINEL_HTTP_DETAIL not in response.text
    for header_value in response.headers.values():
        assert SENTINEL_HTTP_DETAIL not in header_value


async def test_method_not_allowed_preserves_allow_header_without_leaking_detail() -> None:
    """Starlette anexa Allow via HTTPException.headers num 405 real de rota —
    cabecalho de protocolo preservado mesmo substituindo o corpo padrao pelo
    problem+json (revisao CC-106)."""
    app = FastAPI()
    app.add_middleware(RequestIdMiddleware)
    register_exception_handlers(app)

    @app.get("/__test__/only-get")
    async def _only_get() -> dict[str, str]:
        return {}

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/__test__/only-get")

    assert response.status_code == 405
    assert response.headers.get("allow") == "GET"
    assert response.headers["content-type"].startswith("application/problem+json")
