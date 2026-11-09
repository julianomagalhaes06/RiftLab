"""CC-1215: a API reforça a fronteira do loopback (Host, Fetch Metadata, anti-framing)."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from httpx import ASGITransport, AsyncClient

from riftlab.api.app import create_app
from riftlab.api.loopback import LoopbackGuardMiddleware, host_name
from riftlab.config import Settings

UNREACHABLE_DB = "postgresql+asyncpg://riftlab:riftlab@127.0.0.1:1/unused"


@pytest_asyncio.fixture
async def api() -> AsyncIterator[FastAPI]:
    app = create_app(Settings(database_url=UNREACHABLE_DB))
    async with app.router.lifespan_context(app):
        yield app


async def get(app: FastAPI, path: str, base: str = "http://127.0.0.1:8000", **headers: str):
    async with AsyncClient(transport=ASGITransport(app=app), base_url=base) as client:
        return await client.get(path, headers=headers)


def test_defaults_keep_the_api_on_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RIFTLAB_API_HOST", raising=False)
    monkeypatch.delenv("RIFTLAB_API_ALLOWED_HOSTS", raising=False)
    settings = Settings()
    assert settings.api_host == "127.0.0.1"
    assert settings.api_allowed_hosts == ("127.0.0.1", "localhost", "::1")


def test_compose_style_allowlist_is_read_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RIFTLAB_API_ALLOWED_HOSTS", '["127.0.0.1", "localhost", "::1", "api"]')
    assert Settings().api_allowed_hosts == ("127.0.0.1", "localhost", "::1", "api")


@pytest.mark.parametrize(
    ("header", "name"),
    [
        ("127.0.0.1:8000", "127.0.0.1"),
        ("LocalHost", "localhost"),
        ("[::1]:8000", "::1"),
        ("::1", "::1"),
        ("api:8000", "api"),
        ("rebind.example:8000", "rebind.example"),
        ("", ""),
    ],
)
def test_host_name_drops_port_and_case(header: str, name: str) -> None:
    assert host_name(header) == name


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "base", ["http://127.0.0.1:8000", "http://localhost:8000", "http://[::1]:8000"]
)
async def test_loopback_hosts_reach_the_api(api: FastAPI, base: str) -> None:
    response = await get(api, "/health/live", base)
    assert response.status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "base",
    [
        "http://rebind.example:8000",
        "http://192.168.1.50:5173",
        "http://api:8000",
        "http://127.0.0.2:8000",
    ],
)
async def test_other_hosts_are_refused(api: FastAPI, base: str) -> None:
    response = await get(api, "/api/v1/matches", base)
    assert response.status_code == 400
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    assert body["code"] == "HOST_NOT_ALLOWED"
    assert body["requestId"] == response.headers["x-request-id"]
    assert response.headers["x-frame-options"] == "DENY"


@pytest.mark.asyncio
async def test_compose_allowlist_accepts_the_frontend_proxy_host() -> None:
    settings = Settings(
        database_url=UNREACHABLE_DB, api_allowed_hosts=("127.0.0.1", "localhost", "::1", "api")
    )
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        response = await get(app, "/health/live", "http://api:8000")
    assert response.status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("site", ["cross-site", "same-site", "CROSS-SITE"])
async def test_requests_from_other_sites_are_refused(api: FastAPI, site: str) -> None:
    # `<object>`, `<img>`, formulário ou navegação vindos de outra página.
    response = await get(api, "/api/v1/matches/BR1_1", **{"Sec-Fetch-Site": site})
    assert response.status_code == 403
    assert response.json()["code"] == "CROSS_SITE_REQUEST"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "headers", [{}, {"Sec-Fetch-Site": "same-origin"}, {"Sec-Fetch-Site": "none"}]
)
async def test_spa_cli_and_typed_address_pass(api: FastAPI, headers: dict[str, str]) -> None:
    response = await get(api, "/health/live", **headers)
    assert response.status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/health/live", "/docs", "/rota-inexistente"])
async def test_every_response_refuses_framing(api: FastAPI, path: str) -> None:
    response = await get(api, path)
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["content-security-policy"] == "frame-ancestors 'none'"
    assert response.headers["x-content-type-options"] == "nosniff"


@pytest.mark.asyncio
async def test_route_csp_keeps_its_policy_and_gains_frame_ancestors() -> None:
    # Mesmo formato do relatório HTML exportado (api/routes/analyses.py).
    app = FastAPI()
    app.add_middleware(LoopbackGuardMiddleware, allowed_hosts=("127.0.0.1",))

    @app.get("/relatorio")
    async def relatorio() -> HTMLResponse:
        return HTMLResponse(
            "<p>ok</p>",
            headers={"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'"},
        )

    response = await get(app, "/relatorio")
    assert response.headers["content-security-policy"] == (
        "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'"
    )


NAVIGATION = {
    "Sec-Fetch-Site": "cross-site",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Dest": "document",
}


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["google", "discord"])
async def test_return_from_the_social_provider_is_not_refused(api: FastAPI, provider: str) -> None:
    # Sem o cookie da tentativa, a própria rota recusa e manda de volta à tela de entrar.
    response = await get(
        api, f"/api/v1/auth/social/{provider}/callback?state=x&code=y", **NAVIGATION
    )
    assert response.status_code != 403, response.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "headers"),
    [
        ("/api/v1/auth/social/google/callback", {**NAVIGATION, "Sec-Fetch-Dest": "iframe"}),
        (
            "/api/v1/auth/social/google/callback",
            {**NAVIGATION, "Sec-Fetch-Dest": "image", "Sec-Fetch-Mode": "no-cors"},
        ),
        ("/api/v1/auth/social/google/callback", {"Sec-Fetch-Site": "cross-site"}),
        ("/api/v1/auth/social/outro/callback", NAVIGATION),
        ("/api/v1/auth/social/google/start", NAVIGATION),
        ("/api/v1/matches/BR1_1", NAVIGATION),
    ],
)
async def test_only_the_full_page_return_escapes_the_cross_site_guard(
    api: FastAPI, path: str, headers: dict[str, str]
) -> None:
    response = await get(api, path, **headers)
    assert (response.status_code, response.json()["code"]) == (403, "CROSS_SITE_REQUEST")
