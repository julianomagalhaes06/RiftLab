#!/usr/bin/env python3
"""Fumaça do fluxo importar -> partida preparada -> decisões, só pela API HTTP
(sem console, sem banco direto, sem rede externa): importa a partida DEMO,
espera o worker importar e PREPARAR sozinho (catálogo -> reconstrução ->
extração) e lê a lista/detalhe de decisões. Só biblioteca padrão.

Com login exigido (contas locais), cria antes uma conta descartável e a confirma com o código
lido no Mailpit, porque importar é da conta: a sessão vai num cookie e toda chamada leva o
cabeçalho anti-CSRF `X-RiftLab-Request`.

Uso: python3 scripts/smoke_import_flow.py [--base-url http://127.0.0.1:8000]
Sai com código != 0 e uma linha de causa se qualquer etapa falhar."""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import secrets
import sys
import time
import urllib.error
import urllib.request
from typing import Any

from mailpit_code import wait_for_code

MATCH_ID = "DEMO-0008"
PARTICIPANT_ID = 1
PERSONAL_WORDS = ("subject", "hash", "puuid", "riotid")
# Sessão (cookie HttpOnly) compartilhada por todas as chamadas, sem proxy do ambiente.
OPENER = urllib.request.build_opener(
    urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()), urllib.request.ProxyHandler({})
)


def fail(message: str) -> None:
    print(f"FALHA: {message}", file=sys.stderr)
    raise SystemExit(1)


def call(method: str, url: str, body: dict[str, Any] | None = None) -> tuple[int, Any]:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(  # noqa: S310 - URL local fixada pelo chamador
        url,
        data=data,
        method=method,
        headers={"content-type": "application/json", "X-RiftLab-Request": "1"},
    )
    try:
        with OPENER.open(request, timeout=15) as response:
            return response.status, json.loads(response.read() or b"null")
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"null")


def wait_for(description: str, deadline: float, probe: Any) -> Any:
    while time.monotonic() < deadline:
        outcome = probe()
        if outcome is not None:
            return outcome
        time.sleep(1)
    fail(f"tempo esgotado esperando {description}")


def keys(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            found.add(key)
            found |= keys(item)
    elif isinstance(value, list):
        for item in value:
            found |= keys(item)
    return found


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--mailpit-url", default="http://127.0.0.1:8025")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")
    deadline = time.monotonic() + args.timeout

    status, session = call("GET", f"{base}/api/v1/auth/session")
    if status != 200:
        fail(f"GET /auth/session respondeu {status}: {session}")
    if session.get("authRequired"):
        # Importar exige conta: cria uma descartável, só para esta fumaça.
        email = f"smoke_{secrets.token_hex(5)}@example.test"
        password = f"Aa-{secrets.token_urlsafe(16)}"
        status, created = call("POST", f"{base}/api/v1/auth/register", {"email": email, "password": password})
        if status != 202:
            fail(f"POST /auth/register respondeu {status}: {created}")
        code = wait_for_code(email, args.mailpit_url.rstrip("/"))
        status, confirmed = call(
            "POST", f"{base}/api/v1/auth/verify-email", {"email": email, "code": code, "password": password}
        )
        if status != 200 or not confirmed.get("user"):
            fail(f"POST /auth/verify-email respondeu {status}: {confirmed}")
        print("login exigido: conta descartável criada para a fumaça")

    status, accepted = call("POST", f"{base}/api/v1/imports", {"matchId": MATCH_ID, "mode": "DEMO"})
    if status != 202:
        fail(f"POST /imports respondeu {status}: {accepted}")
    print(f"import aceito: job {accepted['jobId']} ({accepted['status']})")

    def import_done() -> dict[str, Any] | None:
        code, job = call("GET", f"{base}{accepted['statusUrl']}")
        if code != 200:
            fail(f"GET {accepted['statusUrl']} respondeu {code}")
        if job["status"] == "FAILED":
            fail(f"importação FAILED errorCode={job.get('errorCode')}")
        return job if job["status"] == "SUCCEEDED" else None

    wait_for("a importação terminar", deadline, import_done)
    print("import SUCCEEDED")

    seen: list[str] = []

    def prepared() -> dict[str, Any] | None:
        code, detail = call("GET", f"{base}/api/v1/matches/{MATCH_ID}")
        if code != 200:
            fail(f"GET /matches/{MATCH_ID} respondeu {code}: {detail}")
        state = detail["preparation"]["status"]
        if not seen or seen[-1] != state:
            seen.append(state)
        if state == "FAILED":
            fail(f"preparação FAILED errorCode={detail['preparation'].get('errorCode')}")
        return detail if state == "READY" else None

    detail = wait_for("a preparação ficar READY", deadline, prepared)
    print(f"preparação: {' -> '.join(seen)}")
    if detail["preparationStatus"] != "READY" or not detail["participants"]:
        fail("detalhe da partida incompleto depois de READY")

    code, page = call("GET", f"{base}/api/v1/matches?limit=100")
    row = next((i for i in page["items"] if i["matchId"] == MATCH_ID), None) if code == 200 else None
    if row is None or row["preparationStatus"] != "READY":
        fail(f"lista de partidas sem {MATCH_ID} READY (HTTP {code})")
    personal = {k for k in keys(page) | keys(detail) if any(w in k.lower() for w in PERSONAL_WORDS)}
    if personal:
        fail(f"campos pessoais na resposta: {sorted(personal)}")

    code, listing = call(
        "GET", f"{base}/api/v1/matches/{MATCH_ID}/decisions?participantId={PARTICIPANT_ID}"
    )
    if code != 200 or not listing["items"]:
        fail(f"lista de decisões vazia ou HTTP {code}")
    decision_id = listing["items"][0]["decisionId"]
    code, decision = call("GET", f"{base}/api/v1/decisions/{decision_id}")
    if code != 200:
        fail(f"detalhe da decisão respondeu {code}")
    items = decision["inventory"]["items"]
    if not decision["versions"]["catalogId"] or any(not isinstance(i["name"], str) for i in items):
        fail("detalhe sem catálogo fixado ou com item sem nome (catálogo demo esperado)")
    print(
        f"decisões: {len(listing['items'])} ponto(s); detalhe {decision_id} "
        f"elegível={decision['eligible']} itens={[i['name'] for i in items]}"
    )
    print("OK")


if __name__ == "__main__":
    main()
