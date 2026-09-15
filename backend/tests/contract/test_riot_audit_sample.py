"""Orquestração do spike (CC-105): sequencial, retomada sem baixar de novo,
exclusão diagnosticada e permanente só em 404, parada em erro permanente (auth/
limite), falha transitória não vira exclusão permanente. Sempre com respostas
sintéticas via httpx.MockTransport."""

from __future__ import annotations

import json

import httpx

from riftlab.ingestion.infrastructure.raw_storage import RawMatchStorage
from riftlab.ingestion.infrastructure.riot_account import RiotAccountConfig
from riftlab.ingestion.infrastructure.riot_audit import discover_seed_match_ids, run_audit_sample
from riftlab.ingestion.infrastructure.riot_transport import RiotFetchBudget, RiotHttpTransport

_SYNTHETIC_ACCOUNT = RiotAccountConfig(
    game_name="ExemploSintetico", tag_line="BR1", platform="BR1", regional_routing="AMERICAS"
)


async def _no_sleep(_seconds: float) -> None:
    return None


def _budget(**overrides: object) -> RiotFetchBudget:
    defaults: dict[str, object] = {
        "max_matches": 10,
        "max_requests": 100,
        "max_attempts_per_request": 2,
        "max_total_seconds": 60.0,
    }
    defaults.update(overrides)
    return RiotFetchBudget(**defaults)  # type: ignore[arg-type]


def _transport(handler, budget: RiotFetchBudget) -> RiotHttpTransport:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return RiotHttpTransport(
        api_key="RGAPI-fake-test-key", budget=budget, client=client, sleep=_no_sleep
    )


async def test_fetches_stores_and_excludes_404(tmp_path) -> None:
    body = json.dumps({"ok": True}).encode()

    def handler(request: httpx.Request) -> httpx.Response:
        if "2000000002" in str(request.url):
            return httpx.Response(404)
        return httpx.Response(200, content=body)

    storage = RawMatchStorage(tmp_path)
    budget = _budget()
    transport = _transport(handler, budget)

    result = await run_audit_sample(
        transport, storage, ["BR1_1000000001", "BR1_2000000002"], budget
    )
    await transport.aclose()

    assert result.fetched == ["BR1_1000000001"]
    assert result.excluded == {"BR1_2000000002": "RIOT_NOT_FOUND"}
    assert storage.status("BR1_1000000001") == "valid"
    assert storage.excluded_reason("BR1_2000000002") == "RIOT_NOT_FOUND"


async def test_skips_already_cached_match_without_hitting_the_network_again(tmp_path) -> None:
    body = json.dumps({"ok": True}).encode()
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, content=body)

    storage = RawMatchStorage(tmp_path)
    budget = _budget()

    transport1 = _transport(handler, budget)
    first_run = await run_audit_sample(transport1, storage, ["BR1_1000000001"], budget)
    await transport1.aclose()
    assert first_run.fetched == ["BR1_1000000001"]
    assert len(calls) == 2  # match + timeline

    transport2 = _transport(handler, budget)
    second_run = await run_audit_sample(transport2, storage, ["BR1_1000000001"], budget)
    await transport2.aclose()

    assert second_run.fetched == []
    assert second_run.skipped_cached == ["BR1_1000000001"]
    assert len(calls) == 2  # nenhuma chamada nova: retomada real, nao so em memoria


async def test_stops_on_auth_error_without_marking_remaining_seeds_excluded(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401)

    storage = RawMatchStorage(tmp_path)
    budget = _budget()
    transport = _transport(handler, budget)

    result = await run_audit_sample(
        transport, storage, ["BR1_1000000001", "BR1_2000000002", "BR1_3000000003"], budget
    )
    await transport.aclose()

    assert result.stopped_early_reason == "RIOT_AUTH_FAILED"
    assert result.fetched == []
    assert result.excluded == {}


async def test_respects_max_matches_budget(tmp_path) -> None:
    body = json.dumps({"ok": True}).encode()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body)

    storage = RawMatchStorage(tmp_path)
    budget = _budget(max_matches=1)
    transport = _transport(handler, budget)

    result = await run_audit_sample(
        transport, storage, ["BR1_1000000001", "BR1_2000000002"], budget
    )
    await transport.aclose()

    assert result.fetched == ["BR1_1000000001"]
    assert result.stopped_early_reason == "MAX_MATCHES_REACHED"


async def test_transient_error_is_not_permanently_excluded(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    storage = RawMatchStorage(tmp_path)
    budget = _budget(max_attempts_per_request=1)
    transport = _transport(handler, budget)

    result = await run_audit_sample(transport, storage, ["BR1_1000000001"], budget)
    await transport.aclose()

    assert result.excluded == {"BR1_1000000001": "UPSTREAM_UNAVAILABLE"}
    # falha transitoria nunca vira exclusao permanente no manifesto: uma proxima
    # execucao pode tentar de novo (status continua "missing", nao registrado)
    assert storage.excluded_reason("BR1_1000000001") is None
    assert storage.status("BR1_1000000001") == "missing"


# ---------------------------------------------------------------------------
# Descoberta pela conta própria (CC-105, retomada): Account-V1 -> paginação
# limitada de Match-V5 by-puuid, e validação de identidade das sementes.
# ---------------------------------------------------------------------------


async def test_discover_seed_match_ids_paginates_dedupes_and_returns_puuid() -> None:
    pages = [["BR1_1000000001", "BR1_1000000002"], ["BR1_1000000002", "BR1_1000000003"], []]

    def handler(request: httpx.Request) -> httpx.Response:
        if "by-riot-id" in str(request.url):
            return httpx.Response(200, content=json.dumps({"puuid": "PUUID-SINTETICO"}).encode())
        page = pages.pop(0)
        return httpx.Response(200, content=json.dumps(page).encode())

    budget = _budget()
    transport = _transport(handler, budget)

    puuid, match_ids = await discover_seed_match_ids(
        transport, _SYNTHETIC_ACCOUNT, queue=420, max_seeds=10, page_size=2
    )
    await transport.aclose()

    assert puuid == "PUUID-SINTETICO"
    # BR1_...002 apareceu em duas paginas (sobreposicao real de offset); deduplicado
    assert match_ids == ["BR1_1000000001", "BR1_1000000002", "BR1_1000000003"]


async def test_discover_seed_match_ids_stops_at_max_seeds_without_extra_pages() -> None:
    calls = {"list": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if "by-riot-id" in str(request.url):
            return httpx.Response(200, content=json.dumps({"puuid": "PUUID-X"}).encode())
        calls["list"] += 1
        # cada pagina teria mais itens do que o pedido, mas o teto e' 2 sementes
        return httpx.Response(200, content=json.dumps(["BR1_1", "BR1_2", "BR1_3"]).encode())

    budget = _budget()
    transport = _transport(handler, budget)

    puuid, match_ids = await discover_seed_match_ids(
        transport, _SYNTHETIC_ACCOUNT, queue=420, max_seeds=2, page_size=3
    )
    await transport.aclose()

    assert match_ids == ["BR1_1", "BR1_2"]
    assert calls["list"] == 1  # nao pede pagina extra alem do necessario


async def test_discover_seed_match_ids_stops_on_empty_page_returns_no_seeds() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "by-riot-id" in str(request.url):
            return httpx.Response(200, content=json.dumps({"puuid": "PUUID-X"}).encode())
        return httpx.Response(200, content=b"[]")

    budget = _budget()
    transport = _transport(handler, budget)

    puuid, match_ids = await discover_seed_match_ids(
        transport, _SYNTHETIC_ACCOUNT, queue=420, max_seeds=50, page_size=20
    )
    await transport.aclose()

    assert match_ids == []


async def test_run_audit_sample_excludes_match_that_does_not_belong_to_expected_account(
    tmp_path,
) -> None:
    other_body = json.dumps({"metadata": {"participants": ["OUTRO-PUUID"]}}).encode()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=other_body)

    storage = RawMatchStorage(tmp_path)
    budget = _budget()
    transport = _transport(handler, budget)

    result = await run_audit_sample(
        transport, storage, ["BR1_1000000001"], budget, expected_puuid="MEU-PUUID"
    )
    await transport.aclose()

    assert result.excluded == {"BR1_1000000001": "ACCOUNT_IDENTITY_MISMATCH"}
    assert result.fetched == []
    assert storage.status("BR1_1000000001") == "missing"  # nunca armazenado


async def test_run_audit_sample_accepts_match_that_belongs_to_expected_account(tmp_path) -> None:
    own_body = json.dumps({"metadata": {"participants": ["MEU-PUUID", "OUTRO"]}}).encode()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=own_body)

    storage = RawMatchStorage(tmp_path)
    budget = _budget()
    transport = _transport(handler, budget)

    result = await run_audit_sample(
        transport, storage, ["BR1_1000000001"], budget, expected_puuid="MEU-PUUID"
    )
    await transport.aclose()

    assert result.fetched == ["BR1_1000000001"]
    assert storage.status("BR1_1000000001") == "valid"


async def test_run_audit_sample_without_expected_puuid_skips_identity_check(tmp_path) -> None:
    """--seeds (sem conta) preserva o comportamento anterior: nenhuma checagem
    de identidade é aplicada quando expected_puuid não é fornecido."""
    body = json.dumps({"metadata": {"participants": ["QUALQUER-COISA"]}}).encode()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body)

    storage = RawMatchStorage(tmp_path)
    budget = _budget()
    transport = _transport(handler, budget)

    result = await run_audit_sample(transport, storage, ["BR1_1000000001"], budget)
    await transport.aclose()

    assert result.fetched == ["BR1_1000000001"]
