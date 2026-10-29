"""HTTP contract, resource isolation, and backpressure of the lightweight sidecar."""

import argparse
import json
import sys
import threading
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from riftlab_objectives.serve_llama import completion_url, make_handler


@pytest.fixture
def service():
    received = []
    entered = threading.Event()
    release = threading.Event()
    options = {"content": '{"priority":"DAMAGE"}', "block": False}

    class Upstream(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            received.append(
                (
                    self.headers.get("Authorization"),
                    json.loads(self.rfile.read(int(self.headers["Content-Length"]))),
                )
            )
            entered.set()
            if options["block"]:
                release.wait(timeout=5)
            body = json.dumps({"content": options["content"]}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    proxy = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        make_handler(
            f"http://127.0.0.1:{upstream.server_port}/completion",
            "fixture-q4",
            "fixture-token",
        ),
    )
    threads = [
        threading.Thread(
            target=s.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
        )
        for s in (upstream, proxy)
    ]
    for thread in threads:
        thread.start()

    def post(body):
        req = urllib.request.Request(
            f"http://127.0.0.1:{proxy.server_port}/interpret",
            json.dumps(body).encode(),
            {"Content-Type": "application/json"},
        )
        try:
            response = urllib.request.build_opener(
                urllib.request.ProxyHandler({})
            ).open(req, timeout=3)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            return response.status, json.load(response)

    yield post, received, options, entered, release
    release.set()
    for server in (proxy, upstream):
        server.shutdown()
        server.server_close()
    for thread in threads:
        thread.join(timeout=2)


def test_contract_does_not_load_model_and_authenticates_upstream(service):
    post, received, *_ = service
    assert post({"text": "quero dano"}) == (
        200,
        {"raw": '{"priority":"DAMAGE"}', "modelVersion": "fixture-q4"},
    )
    authorization, request = received[0]
    assert authorization == "Bearer fixture-token"
    assert request["prompt"].endswith(
        "<|im_start|>user\nquero dano<|im_end|>\n<|im_start|>assistant\n"
    )
    assert request["cache_prompt"] is False
    assert request["n_predict"] == 12
    assert "torch" not in sys.modules and "transformers" not in sys.modules


@pytest.mark.parametrize(
    "body",
    [{"text": "x" * 501}, {"text": ""}, {"text": "x", "url": "http://example.com"}, []],
)
def test_invalid_input_never_reaches_model(service, body):
    post, received, *_ = service
    assert post(body)[0] == 422
    assert received == []


@pytest.mark.parametrize("content", [None, ["DAMAGE"], 123])
def test_invalid_upstream_is_a_provider_failure(service, content):
    post, _, options, *_ = service
    options["content"] = content
    assert post({"text": "quero dano"}) == (502, {"error": "model_unavailable"})


def test_concurrency_has_no_unbounded_generation_queue(service):
    post, received, options, entered, release = service
    options["block"] = True
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(post, {"text": "quero dano"})
        assert entered.wait(timeout=2)
        try:
            assert post({"text": "quero dano"}) == (429, {"error": "busy"})
            assert len(received) == 1
        finally:
            release.set()
        assert first.result(timeout=3)[0] == 200
    assert post({"text": "quero dano"})[0] == 200


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/completion",
        "http://8.8.8.8/completion",
        "http://0.0.0.0/completion",
        "http://127.0.0.1/completion?url=other",
        "http://name:secret@127.0.0.1/completion",
    ],
)
def test_upstream_cannot_be_an_external_or_credential_bearing_url(url):
    with pytest.raises(argparse.ArgumentTypeError):
        completion_url(url)
