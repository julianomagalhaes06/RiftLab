"""CC-1220: Swagger UI e ReDoc (JS de CDN sem SRI) só em desenvolvimento."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from riftlab.api.app import create_app
from riftlab.config import Settings

UNREACHABLE_DB = "postgresql+asyncpg://riftlab:riftlab@127.0.0.1:1/unused"


async def _statuses(environment: str) -> dict[str, int]:
    app = create_app(Settings(database_url=UNREACHABLE_DB, environment=environment))
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://127.0.0.1"
        ) as client:
            return {
                path: (await client.get(path)).status_code
                for path in ("/docs", "/redoc", "/openapi.json")
            }


@pytest.mark.asyncio
async def test_docs_pages_only_in_development() -> None:
    assert await _statuses("development") == {"/docs": 200, "/redoc": 200, "/openapi.json": 200}
    assert await _statuses("production") == {"/docs": 404, "/redoc": 404, "/openapi.json": 200}
