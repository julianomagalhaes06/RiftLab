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
    """Rotas de sprints futuras (analyses, learning-attempts, ...) continuam
    schema-only em riftlab.shared.api; nenhuma delas pode aparecer aqui. imports
    (POST/GET/retry) passou a real no CC-201; as duas rotas de DECISOES (lista por
    partida e detalhe) no CC-204; as de PARTIDAS (lista, detalhe e nova tentativa
    de preparação) no CC-205; análises (POST/GET) no CC-303; interpretação de
    objetivo no CC-504; feedback, relatório, reflexão e progresso no CC-505; prévia e
    expurgo de partida (DELETE com confirmação) no CC-603; sincronização do histórico
    (POST/GET) no CC-901; revisão da partida (F1) no CC-1003; perfil de jogador por Riot ID
    (busca, perfil e sincronização) no CC-1101; perfil da conta e catálogo atual no CC-1202;
    partida ao vivo no CC-1211; aviso de jogando agora no CC-1213; contas locais e sessão
    (Sprint 2, #10), visitante (#14) e vínculo da conta Riot pelo ícone (#12)."""
    schema = _current_schema()
    assert set(schema["paths"].keys()) == {
        "/health/live",
        "/health/ready",
        "/api/v1/imports",
        "/api/v1/imports/{job_id}",
        "/api/v1/imports/{job_id}/retry",
        "/api/v1/matches",
        "/api/v1/matches/{match_id}",
        "/api/v1/matches/{match_id}/preparation",
        "/api/v1/matches/{match_id}/decisions",
        "/api/v1/matches/{match_id}/purge-preview",
        "/api/v1/matches/{match_id}/timeline",
        "/api/v1/matches/{match_id}/review",
        "/api/v1/decisions/{decision_id}",
        "/api/v1/ranked/me",
        "/api/v1/history/sync",
        "/api/v1/players/lookup",
        "/api/v1/players/lookup-from-match",
        "/api/v1/players/me",
        "/api/v1/players/live",
        "/api/v1/players/live/status",
        "/api/v1/catalog/current",
        "/api/v1/players/{player_id}/sync",
        "/api/v1/dashboard",
        "/api/v1/champions/patterns",
        "/api/v1/analyses",
        "/api/v1/analyses/{analysis_id}",
        "/api/v1/learning-attempts",
        "/api/v1/learning-attempts/{attempt_id}",
        "/api/v1/learning-attempts/{attempt_id}/answer",
        "/api/v1/learning-attempts/{attempt_id}/hint",
        "/api/v1/learning-attempts/{attempt_id}/reveal",
        "/api/v1/objectives/interpret",
        "/api/v1/analyses/{analysis_id}/feedback",
        "/api/v1/analyses/{analysis_id}/report",
        "/api/v1/learning-attempts/progress",
        "/api/v1/learning-attempts/{attempt_id}/reflection",
        "/api/v1/auth/session",
        "/api/v1/auth/register",
        "/api/v1/auth/login",
        "/api/v1/auth/verify-email",
        "/api/v1/auth/resend-code",
        "/api/v1/auth/password/forgot",
        "/api/v1/auth/password/reset",
        "/api/v1/auth/logout",
        "/api/v1/auth/social/{provider}/start",
        "/api/v1/auth/social/{provider}/callback",
        "/api/v1/riot-link",
        "/api/v1/riot-link/challenge",
        "/api/v1/riot-link/verify",
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


def test_match_routes_document_order_pagination_and_errors() -> None:
    schema = _current_schema()
    listing = schema["paths"]["/api/v1/matches"]
    detail = schema["paths"]["/api/v1/matches/{match_id}"]
    prepare = schema["paths"]["/api/v1/matches/{match_id}/preparation"]
    purge_preview = schema["paths"]["/api/v1/matches/{match_id}/purge-preview"]
    assert set(listing) == {"get"} and set(purge_preview) == {"get"}  # leituras não escrevem
    assert set(detail) == {"get", "delete"}  # CC-603: o expurgo exige confirmação explícita
    assert {p["name"] for p in detail["delete"]["parameters"]} == {"match_id", "confirm"}
    assert set(detail["delete"]["responses"]) == {"202", "404", "422"}
    assert set(prepare) == {"post"}  # nova tentativa de preparação
    assert set(listing["get"]["responses"]) == {"200", "422"}
    assert set(detail["get"]["responses"]) == {"200", "404", "422"}
    assert set(prepare["post"]["responses"]) == {"202", "404", "422"}
    params = {p["name"]: p for p in listing["get"]["parameters"]}
    assert set(params) == {"cursor", "limit", "scope", "player"}
    assert params["scope"]["schema"]["default"] == "all"
    assert params["scope"]["schema"]["enum"] == ["all", "account", "demo", "account_and_demo"]
    assert (
        params["limit"]["schema"]["default"] == 20 and params["limit"]["schema"]["maximum"] == 100
    )
    # a ordem estável é parte do contrato publicado
    assert "startAt" in listing["get"]["description"] and "matchId" in listing["get"]["description"]


def test_match_response_schemas_expose_no_personal_identifier() -> None:
    """A lista e o detalhe não carregam identificador pessoal nem a classe/item
    comprado: o hash do participante nunca sai da API."""
    schemas = _current_schema()["components"]["schemas"]
    for name in (
        "MatchSummary",
        "MatchDetail",
        "MatchParticipant",
        "MatchPreparation",
        "ChampionAsset",
    ):
        for prop in schemas[name]["properties"]:
            assert not any(w in prop.lower() for w in ("subject", "hash", "puuid", "riotid")), (
                name,
                prop,
            )
    assert set(schemas["MatchParticipant"]["properties"]) == {
        "participantId",
        "championId",
        "team",
        "role",
        "champion",  # CC-208: nome e retrato oficiais, ou null
        "win",
        "kills",
        "deaths",
        "assists",
        "totalMinionsKilled",
        "neutralMinionsKilled",
        "finalItems",
        "finalTrinketItemId",
    }
    assert set(schemas["ChampionAsset"]["properties"]) == {
        "name",
        "dataDragonVersion",
        "imageFile",
    }
    assert "preparationStatus" in schemas["MatchSummary"]["properties"]
    assert set(schemas["PreparationStatus"]["enum"]) == {
        "NOT_REQUESTED",
        "PENDING",
        "RUNNING",
        "READY",
        "FAILED",
    }
    assert "name" in schemas["InventoryItem"]["properties"]


def test_create_import_documents_the_dedicated_riot_key_missing_code() -> None:
    """R2-A (CC-205): o 422 de POST /imports anuncia os dois códigos possíveis."""
    post = _current_schema()["paths"]["/api/v1/imports"]["post"]
    description = post["responses"]["422"]["description"]
    assert "RIOT_KEY_MISSING" in description and "VALIDATION_ERROR" in description
