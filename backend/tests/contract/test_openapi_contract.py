"""OpenAPI real da aplicacao (CC-106): so anuncia rotas de fato montadas,
inclui o schema de erro 503 do /health/ready e nao diverge do arquivo
`backend/openapi.json` versionado (gerado por `cc openapi export`, usado pelo
frontend para gerar tipos TS — ver frontend/package.json,
generate:api-types)."""

from __future__ import annotations

import json
from pathlib import Path

from riftlab.api.app import create_app

BACKEND_ROOT = Path(__file__).resolve().parents[2]
COMMITTED_OPENAPI_PATH = BACKEND_ROOT / "openapi.json"


def _current_schema() -> dict:
    return create_app().openapi()


def test_openapi_only_announces_real_routes() -> None:
    """Rotas de sprints futuras (matches, analyses, learning-attempts, ...)
    continuam schema-only em riftlab.shared.api; nenhuma delas pode aparecer
    aqui. imports (POST/GET/retry) passou a real no CC-201."""
    schema = _current_schema()
    assert set(schema["paths"].keys()) == {
        "/health/live",
        "/health/ready",
        "/api/v1/imports",
        "/api/v1/imports/{job_id}",
        "/api/v1/imports/{job_id}/retry",
    }


def test_openapi_declares_problem_detail_for_ready_503() -> None:
    schema = _current_schema()
    ready_responses = schema["paths"]["/health/ready"]["get"]["responses"]
    assert set(ready_responses.keys()) == {"200", "503"}
    ref = ready_responses["503"]["content"]["application/json"]["schema"]["$ref"]
    assert ref == "#/components/schemas/ProblemDetail"
    assert "ProblemDetail" in schema["components"]["schemas"]
    problem_detail = schema["components"]["schemas"]["ProblemDetail"]
    assert set(problem_detail["required"]) == {
        "type",
        "title",
        "status",
        "code",
        "detail",
        "requestId",
    }


def test_openapi_live_has_no_error_response_documented() -> None:
    """/health/live nunca depende do banco; nao ha caminho de erro a
    documentar (docs/architecture.md: "liveness do processo")."""
    schema = _current_schema()
    live_responses = schema["paths"]["/health/live"]["get"]["responses"]
    assert set(live_responses.keys()) == {"200"}


def test_committed_openapi_json_matches_current_app_schema() -> None:
    """Drift check: `uv run cc openapi export openapi.json` (comando
    reproduzivel, ver backend/README.md) deve produzir exatamente o arquivo
    versionado. Se este teste falhar, o comando precisa ser reexecutado e o
    resultado recommitado antes de considerar o gate de contratos fechado."""
    assert COMMITTED_OPENAPI_PATH.exists(), (
        f"{COMMITTED_OPENAPI_PATH} nao existe; rode "
        "`uv run cc openapi export openapi.json` a partir de backend/."
    )
    committed = json.loads(COMMITTED_OPENAPI_PATH.read_text())
    assert committed == _current_schema()
