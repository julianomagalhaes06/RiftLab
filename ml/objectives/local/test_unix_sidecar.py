"""Real Unix HTTP transport with fictitious generation; no model or Docker startup."""

import stat
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from riftlab.api.app import create_app
from riftlab.config import Settings
from riftlab.objectives.application import build_parser, interpret_objective
from riftlab_objectives.serve_llama import make_handler, unix_listener


def handler():
    return make_handler(
        "http://127.0.0.1:1/completion",
        "fixture-uds",
        relay=lambda payload: {"content": '{"priority":"DAMAGE"}'},
    )


@pytest.mark.asyncio
async def test_unix_transport_reuses_parser_without_tcp_or_environment_proxy(
    tmp_path, monkeypatch
):
    socket = tmp_path / "s"
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("NO_PROXY", "")
    with unix_listener(socket, handler()):
        assert stat.S_IMODE(socket.stat().st_mode) == 0o600
        parser = build_parser("local_model", None, "fixture-uds", str(socket))
        for _ in range(2):
            result = await interpret_objective(parser, "quero dano", 2)
            assert result.as_payload()["objective"]["priority"] == "DAMAGE"
            assert result.provider == "LOCAL_MODEL"
    assert not socket.exists()
    unavailable = await interpret_objective(parser, "quero dano", 2)
    assert unavailable.as_payload()["reasonCodes"] == ["PROVIDER_ERROR"]


@pytest.mark.asyncio
async def test_route_reads_socket_setting_and_returns_form_after_sidecar_stops(
    tmp_path,
):
    socket = tmp_path / "s"
    settings = Settings(
        _env_file=None,
        database_url="postgresql+asyncpg://unused:unused@127.0.0.1:1/unused",
        objective_parser="local_model",
        objective_model_socket_path=str(socket),
        objective_model_version="fixture-uds",
    )
    app = create_app(settings)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
        ) as client,
    ):
        with unix_listener(socket, handler()):
            response = await client.post(
                "/api/v1/objectives/interpret", json={"text": "dano"}
            )
            assert response.status_code == 200
            assert response.json()["objective"] == {
                "priority": "DAMAGE",
                "parserVersion": "fixture-uds",
            }
        unavailable = await client.post(
            "/api/v1/objectives/interpret", json={"text": "dano"}
        )
        assert unavailable.status_code == 200
        assert unavailable.json()["status"] == "NEEDS_INPUT"
        assert unavailable.json()["reasonCodes"] == ["PROVIDER_ERROR"]


def test_unix_listener_preserves_preexisting_file(tmp_path):
    path = tmp_path / "s"
    path.write_text("preexisting content")
    with pytest.raises(OSError), unix_listener(path, handler()):
        pytest.fail("must not replace a preexisting file")
    assert path.read_text() == "preexisting content"
