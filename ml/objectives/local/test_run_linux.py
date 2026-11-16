"""Linux launcher: hash check, private token, readiness, Unix socket and timed shutdown.

A fictitious llama-server stands in for the real one: it answers only to the launcher's token.
No model, GPU or Docker is used.
"""

import hashlib
import json
import os
import socket
import stat
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

RUN = Path(__file__).resolve().parent / "run.py"

FAKE_SERVER = """#!{python}
import json, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
args = sys.argv[1:]
port = int(args[args.index("--port") + 1])
key = open(args[args.index("--api-key-file") + 1]).read().strip()
assert args[args.index("--host") + 1] == "127.0.0.1"

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass
    def reply(self, status, value):
        body = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def do_GET(self):
        self.reply(200, {{"status": "ok"}}) if self.path == "/health" else self.reply(404, {{}})
    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", "0")))
        if self.headers.get("Authorization") != "Bearer " + key:
            return self.reply(401, {{"error": "unauthorized"}})
        self.reply(200, {{"content": '{{"priority":"DAMAGE"}}'}})

HTTPServer(("127.0.0.1", port), Handler).serve_forever()
"""


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def manifest(tmp_path):
    server = tmp_path / "llama-server"
    server.write_text(FAKE_SERVER.format(python=sys.executable))
    server.chmod(0o755)
    (tmp_path / "model.gguf").write_bytes(b"model")
    (tmp_path / "adapter.gguf").write_bytes(b"adapter")
    value = {"platform": "linux", "parserVersion": "fixture-linux"}
    for name, file in (
        ("server", server),
        ("model", tmp_path / "model.gguf"),
        ("adapter", tmp_path / "adapter.gguf"),
    ):
        value[name] = {"path": file.name, "sha256": digest(file)}
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(value))
    return path


def launch(manifest: Path, tmp_path: Path, duration: int, socket_path: Path, port: int):
    return subprocess.Popen(
        [
            sys.executable,
            str(RUN),
            "--manifest",
            str(manifest),
            "--output",
            str(tmp_path / "out"),
            "--duration",
            str(duration),
            "--model-port",
            str(free_port()),
            "--port",
            str(port),
            "--unix-socket",
            str(socket_path),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={**os.environ, "NO_PROXY": "*"},
    )


def test_linux_launcher_serves_with_private_token_and_stops_at_the_deadline(
    manifest, tmp_path
):
    socket_path = tmp_path / "sock" / "interpret.sock"
    port = free_port()
    process = launch(manifest, tmp_path, 5, socket_path, port)
    try:
        assert process.stdout.readline().startswith("Modelo local pronto")
        assert stat.S_IMODE(socket_path.stat().st_mode) == 0o600
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/interpret",
            json.dumps({"text": "quero dano"}).encode(),
            {"Content-Type": "application/json"},
        )
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
            request, timeout=5
        ) as reply:
            body = json.load(reply)
        # O sidecar só obtém a geração com o token que o launcher gerou para o servidor.
        assert body == {"raw": '{"priority":"DAMAGE"}', "modelVersion": "fixture-linux"}
        assert process.wait(timeout=20) == 0
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
    resources = json.loads((tmp_path / "out" / "resources.json").read_text())
    assert resources["stopReason"] == "duration"
    assert not socket_path.exists()
    token = tmp_path / "out" / "api-token"
    assert stat.S_IMODE(token.stat().st_mode) == 0o600


def test_linux_launcher_refuses_a_file_that_does_not_match_the_manifest(
    manifest, tmp_path
):
    (tmp_path / "adapter.gguf").write_bytes(b"other adapter")
    started = time.monotonic()
    process = launch(
        manifest, tmp_path, 5, tmp_path / "sock" / "interpret.sock", free_port()
    )
    _, error = process.communicate(timeout=20)
    assert process.returncode != 0 and "hash mismatch: adapter" in error
    assert not (tmp_path / "out").exists()  # nada iniciado
    assert time.monotonic() - started < 10
