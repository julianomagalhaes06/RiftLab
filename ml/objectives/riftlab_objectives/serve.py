"""CC-504: sidecar local do interpretador (provedor LOCAL_MODEL do backend).

Uso: uv run python -m riftlab_objectives.serve --config CONFIG [--port 8765]
Escuta só em 127.0.0.1. Única rota: POST /interpret {"text": até 500 caracteres} →
{"raw": <saída do modelo>, "modelVersion": ...}. O modelo só gera texto a partir do texto
recebido: não há ferramentas, rede, shell ou segredos ao seu alcance. O backend valida a
saída estritamente; este processo não decide nada.
"""

from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock

import yaml

from riftlab_objectives.evaluate import generate, load

MAX_TEXT = 500
MAX_BODY = 4096


def make_handler(tokenizer, model, version: str, max_new_tokens: int):
    lock = Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args) -> None:  # nunca registrar o texto recebido
            return

        def _send(self, status: int, body: dict) -> None:
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:
            self._send(200, {"status": "UP", "modelVersion": version}) if self.path == "/health" \
                else self._send(404, {"error": "not_found"})

        def do_POST(self) -> None:
            if self.path != "/interpret":
                return self._send(404, {"error": "not_found"})
            length = int(self.headers.get("content-length") or 0)
            if length <= 0 or length > MAX_BODY:
                return self._send(413, {"error": "body_size"})
            try:
                body = json.loads(self.rfile.read(length))
            except ValueError:
                return self._send(400, {"error": "invalid_json"})
            text = body.get("text") if isinstance(body, dict) else None
            if not isinstance(text, str) or not 0 < len(text) <= MAX_TEXT or set(body) != {"text"}:
                return self._send(422, {"error": "invalid_text"})
            with lock:  # um pedido por vez: CPU modesta
                raw = generate(tokenizer, model, text, max_new_tokens)
            self._send(200, {"raw": raw, "modelVersion": version})

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    config = yaml.safe_load(Path(args.config).read_text())
    tokenizer, model = load(config)
    version = config.get("parserVersion") or Path(args.config).stem
    handler = make_handler(tokenizer, model, version, config.get("maxNewTokens", 12))
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler)
    print(f"interpretador {version} em http://127.0.0.1:{args.port}/interpret", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
