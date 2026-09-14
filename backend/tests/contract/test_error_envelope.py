"""Contrato de erro (docs/contracts.md): application/problem+json com type, title,
status, code, detail e requestId; sem DSN/SQL/stack trace no corpo."""


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
