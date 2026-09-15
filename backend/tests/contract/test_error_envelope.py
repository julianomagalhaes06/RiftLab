"""Contrato de erro (docs/contracts.md): application/problem+json com type, title,
status, code, detail e requestId; sem DSN/SQL/stack trace no corpo.

Os testes de 404/422/500 abaixo usam uma FastAPI() de teste isolada (nao
riftlab.api.app.create_app) que so registra riftlab.errors.register_exception_handlers
e uma rota descartavel — "rota apenas de teste", nunca publica na aplicacao real,
conforme o plano do CC-106 ("nao criar endpoint publico para gerar falhas")."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from riftlab.errors import RESOURCE_NOT_FOUND, VALIDATION_ERROR, register_exception_handlers
from riftlab.observability import RequestIdMiddleware
from riftlab.shared.api.imports import ImportRequest

SENTINEL_SECRET = "sentinela-sql-postgresql+asyncpg://riftlab:s3nh4-nunca-real@db/riftlab"  # noqa: S105


def _build_error_probe_app() -> FastAPI:
    """App FastAPI so de teste: mesmos handlers de erro da aplicacao real, mais
    uma rota que valida corpo (422) e uma que sempre lanca (500 com segredo
    sintetico embutido na mensagem, para provar que nunca vaza)."""
    app = FastAPI()
    app.add_middleware(RequestIdMiddleware)
    register_exception_handlers(app)

    @app.post("/__test__/imports")
    async def _echo_import(payload: ImportRequest) -> dict[str, str]:
        return {"matchId": payload.match_id}

    @app.get("/__test__/boom")
    async def _boom() -> None:
        raise RuntimeError(f"falha interna com segredo sintetico: {SENTINEL_SECRET}")

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
