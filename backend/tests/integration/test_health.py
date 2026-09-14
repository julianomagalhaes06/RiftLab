"""Testes de integracao: exigem RIFTLAB_DATABASE_URL apontando para Postgres real
(compose.test.yaml ou servico do CI). O caso de falha nao depende de banco real."""


async def test_live_is_always_up(app_client_with_unreachable_db):
    response = await app_client_with_unreachable_db.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "UP"}


async def test_ready_returns_200_with_real_database(app_client):
    response = await app_client.get("/health/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "UP", "database": "UP"}


async def test_ready_returns_503_when_database_unreachable(app_client_with_unreachable_db):
    response = await app_client_with_unreachable_db.get("/health/ready")
    assert response.status_code == 503
    assert response.headers["content-type"].startswith("application/problem+json")
