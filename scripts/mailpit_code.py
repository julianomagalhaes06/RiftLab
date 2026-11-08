#!/usr/bin/env python3
"""Lê no Mailpit local o código de 6 dígitos mais recente enviado a um e-mail (smoke e CI).

Uso: python3 scripts/mailpit_code.py pessoa@example.test [--base-url http://127.0.0.1:8025]
Só biblioteca padrão.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from typing import Any

OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
CODE = re.compile(r"\b([0-9]{6})\b")


def _get(url: str) -> dict[str, Any]:
    with OPENER.open(url, timeout=5) as response:
        return json.load(response)


def wait_for_code(email: str, base: str = "http://127.0.0.1:8025", timeout: float = 30.0) -> str:
    query = urllib.parse.urlencode({"query": f'to:"{email}"', "limit": 1})
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        found = _get(f"{base}/api/v1/search?{query}").get("messages") or []
        if found:
            text = _get(f"{base}/api/v1/message/{found[0]['ID']}").get("Text", "")
            match = CODE.search(text)
            if match:
                return match.group(1)
        time.sleep(0.5)
    raise TimeoutError(f"nenhum código chegou ao Mailpit em {timeout:.0f} s")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("email")
    parser.add_argument("--base-url", default="http://127.0.0.1:8025")
    args = parser.parse_args()
    try:
        print(wait_for_code(args.email, args.base_url.rstrip("/")))
    except TimeoutError as exc:
        sys.exit(str(exc))
