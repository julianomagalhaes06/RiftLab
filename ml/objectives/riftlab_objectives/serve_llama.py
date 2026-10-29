"""Lightweight LOCAL_MODEL sidecar for an existing loopback llama-server.

Uses only Python's standard library: no torch, transformers, or duplicate model load.
The backend keeps responsibility for validating the model's output and opening the form.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import BoundedSemaphore
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener

from riftlab_objectives.prompt import messages


def completion_url(value: str) -> str:
    parts = urlsplit(value)
    try:
        address = ipaddress.ip_address(parts.hostname or "")
        _ = parts.port
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected a literal local IP address") from exc
    if (
        parts.scheme != "http"
        or not address.is_loopback
        or parts.username
        or parts.password
        or parts.query
        or parts.fragment
        or parts.path != "/completion"
    ):
        raise argparse.ArgumentTypeError("expected http://LOCAL_IP:PORT/completion")
    return value


def make_handler(
    url: str,
    version: str,
    api_key: str | None = None,
    relay: Callable[[dict], dict] | None = None,
):
    available = BoundedSemaphore(1)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args) -> None:
            pass  # Never record objective text or model output.

        def send_json(self, status: int, value: dict) -> None:
            data = json.dumps(value).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_GET(self) -> None:
            self.send_json(
                200, {"status": "UP", "modelVersion": version}
            ) if self.path == "/health" else self.send_json(404, {"error": "not_found"})

        def do_POST(self) -> None:
            if self.path != "/interpret":
                return self.send_json(404, {"error": "not_found"})
            self.connection.settimeout(15)
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                return self.send_json(400, {"error": "invalid_length"})
            if not 0 < length <= 4096 or self.headers.get("Transfer-Encoding"):
                return self.send_json(413, {"error": "body_size"})
            try:
                body = json.loads(self.rfile.read(length))
            except (ValueError, OSError):
                return self.send_json(400, {"error": "invalid_json"})
            text = body.get("text") if isinstance(body, dict) else None
            if (
                not isinstance(text, str)
                or not 0 < len(text) <= 500
                or set(body) != {"text"}
            ):
                return self.send_json(422, {"error": "invalid_text"})
            if not available.acquire(blocking=False):
                return self.send_json(429, {"error": "busy"})
            try:
                prompt = (
                    "".join(
                        f"<|im_start|>{m['role']}\n{m['content']}<|im_end|>\n"
                        for m in messages(text)
                    )
                    + "<|im_start|>assistant\n"
                )
                headers = {"Content-Type": "application/json"}
                if api_key:
                    headers["Authorization"] = "Bearer " + api_key
                payload = {
                    "prompt": prompt,
                    "n_predict": 12,
                    "temperature": 0,
                    "seed": 0,
                    "repeat_penalty": 1,
                    "cache_prompt": False,
                    "stream": False,
                }
                if relay is not None:
                    result = relay(payload)
                else:
                    request = Request(url, json.dumps(payload).encode(), headers)
                    with build_opener(ProxyHandler({})).open(
                        request, timeout=14
                    ) as response:
                        data = response.read(65537)
                    if len(data) > 65536:
                        raise ValueError("upstream body too large")
                    result = json.loads(data)
                raw = result.get("content") if isinstance(result, dict) else None
                if not isinstance(raw, str):
                    raise TypeError("invalid upstream content")
                self.send_json(200, {"raw": raw, "modelVersion": version})
            except (ValueError, TypeError, OSError):
                self.send_json(502, {"error": "model_unavailable"})
            finally:
                available.release()

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--llama-url", type=completion_url, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--api-key-file", type=Path)
    parser.add_argument(
        "--windows-relay",
        type=Path,
        help="PowerShell JSON-lines relay for a model on Windows loopback",
    )
    args = parser.parse_args()
    key = args.api_key_file.read_text().strip() if args.api_key_file else None
    if args.api_key_file and (not key or any(c.isspace() for c in key)):
        parser.error("API key must be a nonempty token without whitespace")
    relay = None
    if args.windows_relay:
        from riftlab_objectives.windows_relay import WindowsRelay

        relay = WindowsRelay(args.llama_url, args.windows_relay, args.api_key_file)
    try:
        with ThreadingHTTPServer(
            ("127.0.0.1", args.port),
            make_handler(args.llama_url, args.version, key, relay),
        ) as server:
            server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if relay:
            relay.close()


if __name__ == "__main__":
    main()
