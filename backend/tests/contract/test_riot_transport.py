"""Contrato do RiotHttpTransport (CC-105, spike de auditoria de acesso real).
Sempre com respostas sintéticas via httpx.MockTransport — nenhuma chamada de rede
real, nenhuma credencial real. Cobre exatamente os caminhos exigidos pelo plano:
sucesso, autenticação inválida, 404, 429/Retry-After, 5xx/timeouts com limite,
payload inválido, rota/ID inválidos, ausência de vazamento em erros."""

from __future__ import annotations

import hashlib
import json

import httpx
import pytest

from riftlab.ingestion.infrastructure.riot_transport import (
    API_KEY_HEADER,
    RiotFetchBudget,
    RiotHttpTransport,
)
from riftlab.shared.errors import (
    SourceAuthError,
    SourceError,
    SourceNotFoundError,
    SourceRateLimitedError,
    SourceSchemaError,
)

_FAKE_API_KEY = "RGAPI-fake-test-key-not-real"


async def _no_sleep(_seconds: float) -> None:
    return None


def _budget(**overrides: object) -> RiotFetchBudget:
    defaults: dict[str, object] = {
        "max_matches": 10,
        "max_requests": 20,
        "max_attempts_per_request": 3,
        "max_total_seconds": 60.0,
    }
    defaults.update(overrides)
    return RiotFetchBudget(**defaults)  # type: ignore[arg-type]


def _transport(
    handler, budget: RiotFetchBudget | None = None, sleep=_no_sleep
) -> RiotHttpTransport:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return RiotHttpTransport(
        api_key=_FAKE_API_KEY, budget=budget or _budget(), client=client, sleep=sleep
    )


async def test_fetch_match_success_sends_key_only_in_header_and_hashes_body() -> None:
    body = json.dumps({"metadata": {"matchId": "BR1_1000000001"}, "info": {}}).encode()
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=body)

    transport = _transport(handler)
    result = await transport.fetch_match("BR1_1000000001")
    await transport.aclose()

    assert result.match_id == "BR1_1000000001"
    assert result.kind == "match"
    assert result.body == body
    assert result.sha256 == hashlib.sha256(body).hexdigest()
    assert len(seen) == 1
    request = seen[0]
    assert request.headers[API_KEY_HEADER] == _FAKE_API_KEY
    assert request.url.host == "americas.api.riotgames.com"
    assert request.url.path == "/lol/match/v5/matches/BR1_1000000001"
    assert _FAKE_API_KEY not in str(request.url)


async def test_fetch_timeline_uses_timeline_path() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=b"{}")

    transport = _transport(handler)
    result = await transport.fetch_timeline("BR1_1000000001")
    await transport.aclose()

    assert result.kind == "timeline"
    assert seen[0].url.path == "/lol/match/v5/matches/BR1_1000000001/timeline"


async def test_invalid_match_id_is_rejected_without_any_http_call() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("nenhuma chamada HTTP deveria ocorrer para match_id invalido")

    transport = _transport(handler)
    with pytest.raises(SourceSchemaError) as excinfo:
        await transport.fetch_match("../etc/passwd")
    await transport.aclose()

    assert excinfo.value.code == "RIOT_INVALID_MATCH_ID"


@pytest.mark.parametrize("status_code", [401, 403])
async def test_auth_failure_raises_immediately_without_retry(status_code: int) -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(status_code)

    transport = _transport(handler)
    with pytest.raises(SourceAuthError) as excinfo:
        await transport.fetch_match("BR1_1000000001")
    await transport.aclose()

    assert excinfo.value.code == "RIOT_AUTH_FAILED"
    assert len(calls) == 1  # falha permanente: nunca tenta de novo


async def test_404_raises_not_found() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    transport = _transport(handler)
    with pytest.raises(SourceNotFoundError) as excinfo:
        await transport.fetch_match("BR1_1000000001")
    await transport.aclose()

    assert excinfo.value.code == "RIOT_NOT_FOUND"


async def test_429_respects_retry_after_and_succeeds_within_budget() -> None:
    body = b'{"ok": true}'
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "2"})
        return httpx.Response(200, content=body)

    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    transport = _transport(handler, sleep=fake_sleep)
    result = await transport.fetch_match("BR1_1000000001")
    await transport.aclose()

    assert result.body == body
    assert slept == [2.0]
    assert len(calls) == 2


async def test_429_without_retry_after_raises_rate_limited_immediately() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429)

    transport = _transport(handler)
    with pytest.raises(SourceRateLimitedError) as excinfo:
        await transport.fetch_match("BR1_1000000001")
    await transport.aclose()

    assert excinfo.value.code == "RIOT_RATE_LIMITED"
    assert excinfo.value.retry_after_seconds is None


async def test_429_exceeding_attempt_budget_raises_rate_limited() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "1"})

    transport = _transport(handler, budget=_budget(max_attempts_per_request=2))
    with pytest.raises(SourceRateLimitedError):
        await transport.fetch_match("BR1_1000000001")
    await transport.aclose()


async def test_5xx_retries_then_raises_upstream_unavailable() -> None:
    attempts: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(request)
        return httpx.Response(503)

    transport = _transport(handler, budget=_budget(max_attempts_per_request=3))
    with pytest.raises(SourceError) as excinfo:
        await transport.fetch_match("BR1_1000000001")
    await transport.aclose()

    assert excinfo.value.code == "UPSTREAM_UNAVAILABLE"
    assert len(attempts) == 3


async def test_5xx_then_success_recovers() -> None:
    body = b'{"ok": true}'
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) < 2:
            return httpx.Response(500)
        return httpx.Response(200, content=body)

    transport = _transport(handler)
    result = await transport.fetch_match("BR1_1000000001")
    await transport.aclose()

    assert result.body == body
    assert len(calls) == 2


async def test_timeout_retries_then_raises_upstream_unavailable() -> None:
    attempts: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(request)
        raise httpx.ConnectTimeout("boom", request=request)

    transport = _transport(handler, budget=_budget(max_attempts_per_request=2))
    with pytest.raises(SourceError) as excinfo:
        await transport.fetch_match("BR1_1000000001")
    await transport.aclose()

    assert excinfo.value.code == "UPSTREAM_UNAVAILABLE"
    assert len(attempts) == 2


async def test_invalid_json_payload_raises_schema_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"{not valid json")

    transport = _transport(handler)
    with pytest.raises(SourceSchemaError) as excinfo:
        await transport.fetch_match("BR1_1000000001")
    await transport.aclose()

    assert excinfo.value.code == "SCHEMA_UNSUPPORTED"


async def test_never_follows_redirect_to_another_host() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": "https://attacker.example/steal"})

    transport = _transport(handler)
    with pytest.raises(SourceError) as excinfo:
        await transport.fetch_match("BR1_1000000001")
    await transport.aclose()

    assert excinfo.value.code == "UPSTREAM_UNAVAILABLE"
    assert "attacker" not in str(excinfo.value)


async def test_local_request_budget_stops_before_exceeding_it() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, content=b"{}")

    transport = _transport(handler, budget=_budget(max_requests=1))
    await transport.fetch_match("BR1_1000000001")
    with pytest.raises(SourceRateLimitedError) as excinfo:
        await transport.fetch_timeline("BR1_1000000001")
    await transport.aclose()

    assert excinfo.value.code == "RIOT_RATE_LIMITED"
    assert len(calls) == 1


@pytest.mark.parametrize("status_code", [401, 404, 429, 500])
async def test_errors_never_leak_the_api_key(status_code: int) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code)

    transport = _transport(handler, budget=_budget(max_attempts_per_request=1))
    with pytest.raises(SourceError) as excinfo:
        await transport.fetch_match("BR1_1000000001")
    await transport.aclose()

    assert _FAKE_API_KEY not in str(excinfo.value)


# ---------------------------------------------------------------------------
# Descoberta pela conta própria (Account-V1 -> Match-V5 by-puuid), CC-105.
# ---------------------------------------------------------------------------


async def test_resolve_account_puuid_success_encodes_segments() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=json.dumps({"puuid": "PUUID-SINTETICO"}).encode())

    transport = _transport(handler)
    puuid = await transport.resolve_account_puuid("Nomeção Ç", "BR 1#")
    await transport.aclose()

    assert puuid == "PUUID-SINTETICO"
    assert len(seen) == 1
    # url.path é decodificado por conveniência do httpx; o encoding real
    # efetivamente enviado fica em raw_path (bytes) — é isso que prova que
    # cada segmento foi codificado individualmente (espaço/# não aparecem
    # crus na representação de fio, mesmo com Unicode nos dois segmentos).
    raw_path = seen[0].url.raw_path
    assert raw_path.startswith(b"/riot/account/v1/accounts/by-riot-id/")
    assert b" " not in raw_path
    assert b"#" not in raw_path
    assert seen[0].url.path == "/riot/account/v1/accounts/by-riot-id/Nomeção Ç/BR 1#"


async def test_resolve_account_puuid_404_raises_not_found() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    transport = _transport(handler)
    with pytest.raises(SourceNotFoundError) as excinfo:
        await transport.resolve_account_puuid("Alguem", "BR1")
    await transport.aclose()

    assert excinfo.value.code == "RIOT_NOT_FOUND"


async def test_resolve_account_puuid_missing_field_raises_schema_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"{}")

    transport = _transport(handler)
    with pytest.raises(SourceSchemaError) as excinfo:
        await transport.resolve_account_puuid("Alguem", "BR1")
    await transport.aclose()

    assert excinfo.value.code == "SCHEMA_UNSUPPORTED"


async def test_list_match_ids_by_puuid_success() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=json.dumps(["BR1_1", "BR1_2"]).encode())

    transport = _transport(handler)
    match_ids = await transport.list_match_ids_by_puuid(
        "PUUID-SINTETICO", queue=420, start=0, count=20
    )
    await transport.aclose()

    assert match_ids == ["BR1_1", "BR1_2"]
    request = seen[0]
    assert "/lol/match/v5/matches/by-puuid/" in request.url.path
    assert request.url.params["queue"] == "420"
    assert request.url.params["count"] == "20"


async def test_list_match_ids_by_puuid_unexpected_format_raises_schema_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=json.dumps({"not": "a list"}).encode())

    transport = _transport(handler)
    with pytest.raises(SourceSchemaError) as excinfo:
        await transport.list_match_ids_by_puuid("PUUID-X", queue=420, start=0, count=20)
    await transport.aclose()

    assert excinfo.value.code == "SCHEMA_UNSUPPORTED"


# ---------------------------------------------------------------------------
# Orçamento de tempo: espera (backoff/Retry-After) e timeout de requisição
# respeitam o tempo restante — bug real encontrado na retomada do CC-105.
# ---------------------------------------------------------------------------


async def test_sleep_that_would_exceed_time_budget_raises_immediately_without_sleeping() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "100"})

    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    budget = _budget(max_total_seconds=5.0, max_attempts_per_request=5)
    transport = _transport(handler, budget=budget, sleep=fake_sleep)

    with pytest.raises(SourceRateLimitedError) as excinfo:
        await transport.fetch_match("BR1_1000000001")
    await transport.aclose()

    assert excinfo.value.code == "RIOT_RATE_LIMITED"
    assert excinfo.value.retry_after_seconds == 100.0
    assert slept == []  # nunca chegou a dormir: 100s excederia os 5s restantes


async def test_short_wait_within_budget_still_sleeps_normally() -> None:
    body = b'{"ok": true}'
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "1"})
        return httpx.Response(200, content=body)

    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    budget = _budget(max_total_seconds=60.0)
    transport = _transport(handler, budget=budget, sleep=fake_sleep)
    result = await transport.fetch_match("BR1_1000000001")
    await transport.aclose()

    assert result.body == body
    assert slept == [1.0]


class _RecordingClient:
    """Duplo de teste mínimo: grava url/headers/params/timeout de cada get(),
    sem transporte real algum. Necessário porque httpx.MockTransport não
    expõe de forma limpa o `timeout=` efetivo passado por chamada."""

    def __init__(self, responses: list[httpx.Response]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, object]] = []

    async def get(self, url, *, headers, params=None, timeout=None):
        self.calls.append({"url": url, "headers": headers, "params": params, "timeout": timeout})
        return self._responses.pop(0)


async def test_request_timeout_is_capped_by_remaining_time_budget() -> None:
    fake_client = _RecordingClient([httpx.Response(200, content=b"{}")])
    budget = RiotFetchBudget(
        max_matches=10,
        max_requests=10,
        max_attempts_per_request=1,
        max_total_seconds=2.0,
        request_timeout_seconds=10.0,
    )
    transport = RiotHttpTransport(api_key=_FAKE_API_KEY, budget=budget, client=fake_client)  # type: ignore[arg-type]

    await transport.fetch_match("BR1_1000000001")

    used_timeout = fake_client.calls[0]["timeout"]
    assert used_timeout <= 2.0  # nunca os 10s configurados: capado pelo orçamento total restante


async def test_request_timeout_uses_configured_value_when_budget_has_plenty_of_time() -> None:
    fake_client = _RecordingClient([httpx.Response(200, content=b"{}")])
    budget = RiotFetchBudget(
        max_matches=10,
        max_requests=10,
        max_attempts_per_request=1,
        max_total_seconds=300.0,
        request_timeout_seconds=10.0,
    )
    transport = RiotHttpTransport(api_key=_FAKE_API_KEY, budget=budget, client=fake_client)  # type: ignore[arg-type]

    await transport.fetch_match("BR1_1000000001")

    used_timeout = fake_client.calls[0]["timeout"]
    assert used_timeout == pytest.approx(10.0, abs=0.5)


# ---------------------------------------------------------------------------
# CC-206: limitação global opcional e janela de datas na listagem
# ---------------------------------------------------------------------------


class _FakeClock:
    def __init__(self) -> None:
        self.now = 5000.0

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += seconds


def _limited_transport(handler, limiter, budget: RiotFetchBudget | None = None):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return RiotHttpTransport(
        api_key=_FAKE_API_KEY,
        budget=budget or _budget(),
        client=client,
        sleep=_no_sleep,
        rate_limiter=limiter,
    )


async def test_list_match_ids_sends_time_window_only_when_given() -> None:
    seen: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(dict(request.url.params))
        return httpx.Response(200, content=b"[]")

    transport = _transport(handler)
    await transport.list_match_ids_by_puuid("p", queue=420, start=0, count=100)
    await transport.list_match_ids_by_puuid(
        "p", queue=420, start=100, count=100, start_time=1_700_000_000, end_time=1_700_100_000
    )

    assert seen[0] == {"queue": "420", "start": "0", "count": "100"}
    assert seen[1]["startTime"] == "1700000000" and seen[1]["endTime"] == "1700100000"


async def test_global_limiter_paces_every_kind_of_call_and_learns_headers() -> None:
    from riftlab.ingestion.infrastructure.riot_rate_limiter import RateWindow, RiotRateLimiter

    clock = _FakeClock()
    limiter = RiotRateLimiter(
        app_windows=(RateWindow(1000, 1.0),), safety_margin=1.0, clock=clock, sleep=clock.sleep
    )
    times: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        times.append(clock.now)
        return httpx.Response(
            200,
            content=b"[]",
            headers={"X-App-Rate-Limit": "2:10", "X-App-Rate-Limit-Count": "1:10"},
        )

    transport = _limited_transport(handler, limiter)
    for _ in range(3):
        await transport.list_match_ids_by_puuid("p", queue=420, start=0, count=5)

    assert times[1] - times[0] < 1  # 2 por 10 s: as duas primeiras seguem juntas
    assert times[2] - times[0] >= 9.9  # a 3ª espera a janela aprendida do header
    assert transport.requests_made == 3


async def test_429_retry_after_penalizes_all_later_calls_not_only_the_retry() -> None:
    from riftlab.ingestion.infrastructure.riot_rate_limiter import RateWindow, RiotRateLimiter

    clock = _FakeClock()
    limiter = RiotRateLimiter(app_windows=(RateWindow(1000, 1.0),), clock=clock, sleep=clock.sleep)
    responses = iter(
        [httpx.Response(429, headers={"Retry-After": "40"}), httpx.Response(200, content=b"[]")]
    )
    sent_at: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent_at.append(clock.now)
        return next(responses)

    transport = _limited_transport(handler, limiter)
    await transport.list_match_ids_by_puuid(
        "p", queue=420, start=0, count=5
    )  # 429 -> espera -> 200

    assert sent_at[1] - sent_at[0] >= 40  # o limitador segurou a nova tentativa
    assert transport.requests_made == 2  # a nova tentativa CONTOU no orçamento


async def test_limiter_wait_beyond_remaining_time_stops_resumably_without_a_request() -> None:
    from riftlab.ingestion.infrastructure.riot_rate_limiter import RateWindow, RiotRateLimiter

    clock = _FakeClock()
    limiter = RiotRateLimiter(app_windows=(RateWindow(1000, 1.0),), clock=clock, sleep=clock.sleep)
    limiter.penalize(600)
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(200, content=b"[]")

    transport = _limited_transport(handler, limiter, _budget(max_total_seconds=30.0))

    with pytest.raises(SourceRateLimitedError) as excinfo:
        await transport.list_match_ids_by_puuid("p", queue=420, start=0, count=5)

    assert excinfo.value.retry_after_seconds and excinfo.value.retry_after_seconds >= 600
    assert calls == [] and transport.requests_made == 0  # nenhuma requisição gasta


async def test_transport_silences_the_http_client_url_logging(caplog) -> None:
    """Achado do CC-206: o INFO do httpx registra a URL COMPLETA (Riot ID em Account-V1,
    PUUID na listagem). Todo transporte da Riot o silencia, mesmo se o log raiz estiver em
    DEBUG — o identificador nunca chega a nenhum handler."""
    import logging

    caplog.set_level(logging.DEBUG)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"puuid": "puuid-sintetico-secreto"})

    transport = _transport(handler)
    await transport.resolve_account_puuid("NomeSinteticoSecreto", "TAG")

    assert "NomeSinteticoSecreto" not in caplog.text
    assert "by-riot-id" not in caplog.text


async def test_transport_counts_http_statuses_per_call_kind_without_identifiers() -> None:
    statuses = iter([429, 200, 404])

    def handler(request: httpx.Request) -> httpx.Response:
        status = next(statuses)
        return httpx.Response(
            status, content=b"[]", headers={"Retry-After": "1"} if status == 429 else {}
        )

    transport = _transport(handler)
    await transport.list_match_ids_by_puuid("p", queue=420, start=0, count=5)  # 429 -> 200
    with pytest.raises(SourceNotFoundError):
        await transport.fetch_match("BR1_1000000001")

    counts = transport.status_counts()

    assert counts == {"match:404": 1, "match-list:200": 1, "match-list:429": 1}
    assert "p" not in str(counts) and "BR1_1000000001" not in str(counts)
