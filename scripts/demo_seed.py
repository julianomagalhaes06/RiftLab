#!/usr/bin/env python3
"""Prepara a demonstração (CC-605) só pela API HTTP local: importa as partidas DEMO
sintéticas e espera cada uma ficar pronta. Sem rede externa, sem chave, só biblioteca padrão.

Uso: python3 scripts/demo_seed.py [--base-url http://127.0.0.1:8000] [--timeout 180]
Sai com código != 0 e a causa se alguma etapa falhar. DEMO-0006 fica de fora de propósito:
ela falha na importação (patch ausente) e serve para mostrar o erro pela interface.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from typing import Any

MATCHES = (
    "DEMO-0001", "DEMO-0002", "DEMO-0003", "DEMO-0004", "DEMO-0005", "DEMO-0007", "DEMO-0008",
    "DEMO-0009", "DEMO-0010", "DEMO-0011",
)


def call(method: str, url: str, body: dict[str, Any] | None = None) -> tuple[int, Any]:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(  # noqa: S310 - URL local fixada pelo chamador
        url, data=data, method=method, headers={"content-type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:  # noqa: S310
            return response.status, json.loads(response.read() or b"null")
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"null")


def wait(check: Any, timeout: float, what: str) -> Any:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = check()
        if result is not None:
            return result
        time.sleep(1)
    raise SystemExit(f"FALHA: tempo esgotado esperando {what}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--timeout", type=float, default=180)
    args = parser.parse_args()
    base = args.base_url.rstrip("/")
    for match_id in MATCHES:
        status, accepted = call("POST", f"{base}/api/v1/imports", {"matchId": match_id, "mode": "DEMO"})
        if status != 202:
            raise SystemExit(f"FALHA: importação de {match_id} respondeu {status}")

        def imported() -> Any:
            _, job = call("GET", f"{base}{accepted['statusUrl']}")
            if job["status"] == "FAILED":
                raise SystemExit(f"FALHA: importação de {match_id} terminou em FAILED ({job['errorCode']})")
            return job if job["status"] == "SUCCEEDED" else None

        wait(imported, args.timeout, f"importação de {match_id}")

        def prepared() -> Any:
            _, match = call("GET", f"{base}/api/v1/matches/{match_id}")
            state = match["preparation"]["status"] if isinstance(match, dict) else None
            if state == "FAILED":
                raise SystemExit(f"FALHA: preparação de {match_id} falhou")
            return match if state == "READY" else None

        wait(prepared, args.timeout, f"preparação de {match_id}")
        print(f"ok {match_id}")


if __name__ == "__main__":
    main()
