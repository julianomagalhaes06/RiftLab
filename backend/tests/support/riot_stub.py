"""Servidor Riot FALSO em memória para os testes da coleta (CC-206), para usar com
`httpx.MockTransport`: contas, históricos, partidas e timelines sintéticos, respostas
roteirizadas (429/5xx/401) e um registro de todas as chamadas. Nenhuma rede, nenhuma
credencial real, nenhum dado pessoal real — os "Riot IDs" e PUUIDs abaixo são
sentinelas sintéticas, também usadas para provar que nenhuma saída os vaza."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import unquote

import httpx

API_KEY = "RGAPI-chave-sintetica-de-teste-nao-real"
HASH_SECRET = "segredo-de-hash-sintetico-de-teste"  # noqa: S105
SENTINELS = (API_KEY, HASH_SECRET)


def real_pair(
    match_id: str,
    *,
    seed_puuid: str,
    patch: str = "16.17",
    queue: int = 420,
    map_id: int = 11,
    result: str = "GameComplete",
    other_puuid: str | None = None,
    purchases: tuple[tuple[int, int], ...] = (),
    other_purchases: tuple[tuple[int, int], ...] = (),
    champion_id: int = 89,
    role: str = "UTILITY",
    start_ms: int = 1_700_000_000_000,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Match-V5/Timeline-V5 mínimos e VÁLIDOS. O participante 1 é a semente."""
    other = other_puuid or f"puuid-outro-{match_id}"
    participants = [
        {
            "participantId": 1,
            "puuid": seed_puuid,
            "championId": champion_id,
            "teamId": 100,
            "teamPosition": role,
        },
        {
            "participantId": 2,
            "puuid": other,
            "championId": 51,
            "teamId": 200,
            "teamPosition": "BOTTOM",
        },
    ]
    match_doc = {
        "metadata": {"matchId": match_id, "participants": [seed_puuid, other]},
        "info": {
            "queueId": queue,
            "mapId": map_id,
            "gameVersion": f"{patch}.123.4567",
            "gameStartTimestamp": start_ms,
            "gameDuration": 1800,
            "endOfGameResult": result,
            "participants": participants,
        },
    }
    events = [
        {"timestamp": at, "type": "ITEM_PURCHASED", "participantId": who, "itemId": item}
        for who, group in ((1, purchases), (2, other_purchases))
        for at, item in group
    ]
    timeline_doc = {
        "metadata": {"matchId": match_id},
        "info": {
            "frameInterval": 60000,
            "participants": [
                {"participantId": 1, "puuid": seed_puuid},
                {"participantId": 2, "puuid": other},
            ],
            "frames": [
                {"timestamp": 0, "participantFrames": {}, "events": []},
                {
                    "timestamp": 60000,
                    "participantFrames": {"1": {"currentGold": 500, "level": 3}},
                    "events": events,
                },
            ],
        },
    }
    return match_doc, timeline_doc


@dataclass
class RiotStub:
    accounts: dict[str, str] = field(default_factory=dict)  # gameName -> puuid
    history: dict[str, list[str]] = field(default_factory=dict)  # puuid -> ids (mais novo 1º)
    matches: dict[str, bytes | None] = field(default_factory=dict)  # None = 404
    timelines: dict[str, bytes | None] = field(default_factory=dict)
    calls: list[tuple[str, str]] = field(default_factory=list)  # (kind, chave)
    list_params: list[dict[str, str]] = field(default_factory=list)
    _scripts: dict[str, list[Callable[[], httpx.Response]]] = field(default_factory=dict)
    _auth_fail_after: int | None = None
    response_headers: dict[str, str] = field(default_factory=dict)

    # ---- montagem ------------------------------------------------------------
    def add_account(self, game_name: str, puuid: str) -> None:
        self.accounts[game_name] = puuid
        self.history.setdefault(puuid, [])

    def add_match(
        self, match_id: str, *, seed_puuid: str, register_in_history: bool = True, **overrides: Any
    ) -> None:
        match_doc, timeline_doc = real_pair(match_id, seed_puuid=seed_puuid, **overrides)
        self.matches[match_id] = json.dumps(match_doc).encode()
        self.timelines[match_id] = json.dumps(timeline_doc).encode()
        if register_in_history:
            self.history.setdefault(seed_puuid, []).append(match_id)

    def script(self, key: str, *responses: Callable[[], httpx.Response]) -> None:
        """Respostas consumidas NA ORDEM antes do comportamento normal. `key`:
        'match:<id>', 'timeline:<id>', 'list', 'account' ou 'any'."""
        self._scripts.setdefault(key, []).extend(responses)

    def fail_auth_after(self, calls: int) -> None:
        """Depois de `calls` chamadas, tudo devolve 401 (chave expirada) até `fix_auth()`."""
        self._auth_fail_after = calls

    def fix_auth(self) -> None:
        self._auth_fail_after = None

    # ---- consulta ------------------------------------------------------------
    def count(self, kind: str, key: str | None = None) -> int:
        return sum(1 for k, v in self.calls if k == kind and (key is None or v == key))

    # ---- transporte ----------------------------------------------------------
    def __call__(self, request: httpx.Request) -> httpx.Response:
        kind, key = self._classify(request)
        self.calls.append((kind, key))
        if kind == "list":
            self.list_params.append(dict(request.url.params))
        if self._auth_fail_after is not None and len(self.calls) > self._auth_fail_after:
            return httpx.Response(401)
        for script_key in ("any", kind, f"{kind}:{key}"):
            queue = self._scripts.get(script_key)
            if queue:
                return queue.pop(0)()
        return self._respond(kind, key, request)

    def _classify(self, request: httpx.Request) -> tuple[str, str]:
        path = request.url.path
        if "/accounts/by-riot-id/" in path:
            name = unquote(path.split("/by-riot-id/")[1].split("/")[0])
            return "account", name
        if "/by-puuid/" in path and path.endswith("/ids"):
            return "list", unquote(path.split("/by-puuid/")[1].rsplit("/ids", 1)[0])
        if path.endswith("/timeline"):
            return "timeline", path.rsplit("/", 2)[-2]
        return "match", path.rsplit("/", 1)[-1]

    def _respond(self, kind: str, key: str, request: httpx.Request) -> httpx.Response:
        headers = self.response_headers
        if kind == "account":
            puuid = self.accounts.get(key)
            if puuid is None:
                return httpx.Response(404, headers=headers)
            return httpx.Response(200, json={"puuid": puuid}, headers=headers)
        if kind == "list":
            ids = self.history.get(key, [])
            start = int(request.url.params.get("start", "0"))
            count = int(request.url.params.get("count", "20"))
            return httpx.Response(200, json=ids[start : start + count], headers=headers)
        store = self.matches if kind == "match" else self.timelines
        body = store.get(key)
        if body is None:
            return httpx.Response(404, headers=headers)
        return httpx.Response(200, content=body, headers=headers)


def status_response(status: int, **headers: str) -> Callable[[], httpx.Response]:
    return lambda: httpx.Response(
        status, headers={k.replace("_", "-"): v for k, v in headers.items()}
    )
