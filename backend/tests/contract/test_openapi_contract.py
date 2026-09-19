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
    """Rotas de sprints futuras (GET /matches e /matches/{id}, analyses,
    learning-attempts, ...) continuam schema-only em riftlab.shared.api; nenhuma
    delas pode aparecer aqui. imports (POST/GET/retry) passou a real no CC-201;
    as duas rotas de DECISOES (lista por partida e detalhe) no CC-204."""
    schema = _current_schema()
    assert set(schema["paths"].keys()) == {
        "/health/live",
        "/health/ready",
        "/api/v1/imports",
        "/api/v1/imports/{job_id}",
        "/api/v1/imports/{job_id}/retry",
        "/api/v1/matches/{match_id}/decisions",
        "/api/v1/decisions/{decision_id}",
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


def test_decisions_routes_are_read_only_and_document_their_errors() -> None:
    schema = _current_schema()
    listing = schema["paths"]["/api/v1/matches/{match_id}/decisions"]
    detail = schema["paths"]["/api/v1/decisions/{decision_id}"]
    assert set(listing) == {"get"} and set(detail) == {"get"}  # nenhuma escrita
    assert set(listing["get"]["responses"]) == {"200", "404", "409", "422"}
    assert set(detail["get"]["responses"]) == {"200", "404", "422"}
    params = {p["name"]: p for p in listing["get"]["parameters"]}
    assert set(params) == {"match_id", "participantId", "cursor", "limit"}
    assert params["participantId"]["required"] is True
    assert (
        params["limit"]["schema"]["default"] == 20 and params["limit"]["schema"]["maximum"] == 100
    )


def test_decision_response_schemas_expose_no_purchase_information() -> None:
    """A restricao pedagogica vale no CONTRATO publicado, nao so nos schemas
    internos: nenhum campo do OpenAPI menciona o item comprado, o alvo, o bloco
    retrospectivo, features ou hash pessoal."""
    schemas = _current_schema()["components"]["schemas"]
    names = {
        name
        for name in schemas
        if name.startswith(("Decision", "Inventory", "ObservedLevel", "CursorPage_Decision"))
    }
    assert {"DecisionState", "DecisionStateSummary", "DecisionInventory", "DecisionBudget"} <= names
    forbidden = ("target", "purchased", "retrospective", "feature", "quality", "subject", "hash")
    allowed = {("DecisionVersionInfo", "featureSchemaVersion")}  # só o RÓTULO da versão do schema
    for name in names:
        for prop in schemas[name].get("properties", {}):
            if (name, prop) in allowed:
                continue
            assert not any(word in prop.lower() for word in forbidden), (name, prop)
