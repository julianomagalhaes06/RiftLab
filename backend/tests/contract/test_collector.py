"""Coletor incremental (CC-206) com servidor Riot FALSO: nenhuma rede, nenhuma chave
real. Cobre o que o plano exige: retomada pelo cursor sem repetir, duplicata, tetos,
filtros com contagem de perdas, 429 com Retry-After (global e persistido), 5xx, 401 no
meio da execução com parada e retomada após a troca da chave, crash entre o fetch e a
gravação, e a garantia de que nenhuma saída carrega dado pessoal."""

from __future__ import annotations

import json
import logging

import httpx
import pytest

from riftlab.collection.collector import (
    CollectionCeilings,
    StopReason,
)
from riftlab.collection.filters import MatchFilterConfig
from riftlab.collection.manifest import CollectionManifest, ManifestLockedError, SeedCursor
from riftlab.collection.report import build_collection_report, render_collection_report
from riftlab.ingestion.application import raw_pair_available
from riftlab.ingestion.infrastructure.riot_rate_limiter import RateWindow, RiotRateLimiter
from riftlab.ingestion.infrastructure.subject_hash import (
    SubjectHashSecretMissingError,
    compute_real_subject_hash,
)
from support.collection_harness import (
    NAME_A,
    PERSONAL_SENTINELS,
    SEED_A_PUUID,
    SEED_B_PUUID,
    one_seed,
    two_seeds,
)
from support.riot_stub import HASH_SECRET, real_pair, status_response

# ---------------------------------------------------------------------------
# caminho feliz, duplicata e idempotência
# ---------------------------------------------------------------------------


async def test_collects_dedupes_across_seeds_and_accounts_for_every_request(tmp_path) -> None:
    h = two_seeds(tmp_path)
    for match_id in ("BR1_1001", "BR1_1002"):
        h.stub.add_match(match_id, seed_puuid=SEED_A_PUUID)
    # a mesma partida aparece no histórico das DUAS sementes (as duas jogaram juntas)
    h.stub.add_match("BR1_1003", seed_puuid=SEED_A_PUUID, other_puuid=SEED_B_PUUID)
    h.stub.add_match("BR1_2001", seed_puuid=SEED_B_PUUID)
    h.stub.history[SEED_B_PUUID].insert(0, "BR1_1003")

    report = await h.run()

    assert report.stop_reason == StopReason.COMPLETED
    assert (report.listed, report.new_valid, report.duplicates) == (5, 4, 1)
    assert dict(report.excluded) == {}
    # cada semente: conta + página de dados + página vazia (fim); 2 requisições por partida nova
    assert len(h.stub.calls) == report.requests == 14
    assert h.stub.count("match", "BR1_1003") == 1  # a duplicata não custou requisição
    assert h.manifest().requests_used_total == 14
    for match_id in ("BR1_1001", "BR1_1002", "BR1_1003", "BR1_2001"):
        assert raw_pair_available(h.raw_store, match_id)
    assert h.manifest().fetched_by_seed() == {"seed-01": 3, "seed-02": 1}
    assert report.per_seed["seed-02"].duplicates == 1


async def test_second_run_is_idempotent_and_only_pays_for_the_listing(tmp_path) -> None:
    h = one_seed(tmp_path, ["BR1_1", "BR1_2", "BR1_3"])
    await h.run()
    calls_before = len(h.stub.calls)

    report = await h.run()

    assert report.new_valid == 0 and report.duplicates == 3 and report.stop_reason == "COMPLETED"
    assert len(h.stub.calls) - calls_before == 3  # conta + página + página vazia
    assert h.stub.count("match") == 3 and h.stub.count("timeline") == 3  # nada repetido
    assert h.manifest().cursor("seed-01").passes_completed == 2
    assert h.manifest().fetched_ids() == ["BR1_1", "BR1_2", "BR1_3"]


# ---------------------------------------------------------------------------
# cursor, tetos e retomada
# ---------------------------------------------------------------------------


async def test_match_ceiling_stops_and_the_next_run_resumes_without_refetching(tmp_path) -> None:
    h = one_seed(tmp_path, ["BR1_1", "BR1_2", "BR1_3", "BR1_4"])

    first = await h.run(ceilings=CollectionCeilings(500, 2, 300))

    assert first.stop_reason == StopReason.MAX_MATCHES and first.new_valid == 2
    assert h.manifest().cursor("seed-01") == SeedCursor(passes_completed=0, exhausted=False)
    calls_before = len(h.stub.calls)
    second = await h.run()

    assert second.new_valid == 2 and second.stop_reason == StopReason.COMPLETED
    # a passada recomeça da posição 0 (a listagem cresce pela cabeça): as 2 já conhecidas
    # aparecem como duplicadas, a custo zero, e só as novas são baixadas
    assert h.stub.list_params[1]["start"] == "0"  # 1ª listagem da 2ª execução
    assert second.duplicates == 2 and second.listed == 4
    for match_id in ("BR1_1", "BR1_2"):
        assert h.stub.count("match", match_id) == 1  # nada foi baixado duas vezes
    assert len(h.manifest().fetched_ids()) == 4
    # custo da 2ª execução: conta + página + 2x(partida+timeline) + página vazia (fim)
    assert len(h.stub.calls) - calls_before == 1 + 1 + 4 + 1
    assert h.manifest().cursor("seed-01") == SeedCursor(passes_completed=1, exhausted=True)


async def test_a_match_played_between_runs_enters_at_the_head_and_is_not_skipped(tmp_path) -> None:
    """Achado da Rodada 2b: com cursor posicional, partida nova (índice 0) deslocava a
    lista e a retomada a pulava até a passada seguinte."""
    h = one_seed(tmp_path, ["BR1_1", "BR1_2", "BR1_3", "BR1_4"])
    first = await h.run(ceilings=CollectionCeilings(500, 2, 300))
    assert first.new_valid == 2  # BR1_1 e BR1_2 (as mais novas)

    h.stub.add_match("BR1_9", seed_puuid=SEED_A_PUUID, register_in_history=False)
    h.stub.history[SEED_A_PUUID].insert(0, "BR1_9")  # jogada DEPOIS da 1ª execução

    second = await h.run()

    assert second.stop_reason == StopReason.COMPLETED and second.new_valid == 3
    assert set(h.manifest().fetched_ids()) == {"BR1_1", "BR1_2", "BR1_3", "BR1_4", "BR1_9"}
    for match_id in ("BR1_1", "BR1_2"):
        assert h.stub.count("match", match_id) == 1  # as conhecidas custaram 0 requisição
        assert h.stub.count("timeline", match_id) == 1


async def test_narrowing_the_window_at_the_new_end_does_not_skip_older_matches(tmp_path) -> None:
    """Mudar a janela (`--until` mais antigo tira as MAIS NOVAS da listagem) mudaria a
    lista sob um índice guardado: a retomada pularia partidas. Sem posição, não pula."""
    h = one_seed(tmp_path, ["BR1_1", "BR1_2", "BR1_3", "BR1_4", "BR1_5"])
    first = await h.run(ceilings=CollectionCeilings(500, 2, 300), end_time=None)
    assert first.new_valid == 2

    # a janela nova exclui BR1_1 e BR1_2 (mais novas que --until): a lista vira [3, 4, 5]
    h.stub.history[SEED_A_PUUID] = ["BR1_3", "BR1_4", "BR1_5"]
    second = await h.run(end_time=1_700_000_000)

    assert second.new_valid == 3 and second.stop_reason == StopReason.COMPLETED
    assert set(h.manifest().fetched_ids()) == {f"BR1_{n}" for n in range(1, 6)}
    assert h.stub.list_params[-2]["endTime"] == "1700000000"  # a janela nova foi enviada


async def test_transient_failure_does_not_lose_the_match_the_next_pass_lists_it_again(
    tmp_path,
) -> None:
    h = one_seed(tmp_path, ["BR1_1", "BR1_2", "BR1_3"])
    h.stub.script("match:BR1_2", *[status_response(503)] * 3)
    first = await h.run()
    assert first.stop_reason == StopReason.PARTIAL and first.new_valid == 1
    assert h.manifest().cursor("seed-01").exhausted is False  # passada aberta

    second = await h.run()

    assert second.new_valid == 2 and second.stop_reason == StopReason.COMPLETED
    assert h.manifest().cursor("seed-01").exhausted is True


async def test_request_ceiling_never_overspends_and_resumes_without_duplicates(tmp_path) -> None:
    h = one_seed(tmp_path, ["BR1_1", "BR1_2", "BR1_3"])

    first = await h.run(
        ceilings=CollectionCeilings(max_requests=6, max_matches=50, max_duration_seconds=300)
    )

    assert first.stop_reason == StopReason.MAX_REQUESTS
    assert len(h.stub.calls) <= 6 and first.requests == len(h.stub.calls)
    assert first.new_valid == 2  # conta + lista + 2 partidas (4) = 6
    second = await h.run()
    assert second.new_valid == 1 and h.stub.count("match", "BR1_1") == 1


async def test_duration_ceiling_stops_with_its_own_reason(tmp_path) -> None:
    h = one_seed(tmp_path, ["BR1_1", "BR1_2"])
    h.stub.script(
        "list", lambda: (h.time.advance(30), httpx.Response(200, json=["BR1_1", "BR1_2"]))[1]
    )

    report = await h.run(ceilings=CollectionCeilings(500, 50, max_duration_seconds=10))

    assert report.stop_reason == StopReason.MAX_DURATION and report.new_valid == 0


async def test_per_seed_cap_balances_seeds_and_is_reported_as_not_completed(tmp_path) -> None:
    h = two_seeds(tmp_path)
    for index in range(3):
        h.stub.add_match(f"BR1_{100 + index}", seed_puuid=SEED_A_PUUID)
        h.stub.add_match(f"BR1_{200 + index}", seed_puuid=SEED_B_PUUID)

    report = await h.run(ceilings=CollectionCeilings(500, 50, 300, max_matches_per_seed=1))

    assert report.stop_reason == StopReason.MAX_MATCHES_PER_SEED
    assert {label: stats.new_valid for label, stats in report.per_seed.items()} == {
        "seed-01": 1,
        "seed-02": 1,
    }
    assert h.manifest().cursor("seed-01").exhausted is False  # passada aberta: retoma do início


# ---------------------------------------------------------------------------
# filtros e perdas
# ---------------------------------------------------------------------------


async def test_filters_count_each_loss_and_never_store_or_fetch_the_timeline(tmp_path) -> None:
    h = one_seed(tmp_path, ["BR1_100"])  # a válida
    h.stub.add_match("BR1_101", seed_puuid=SEED_A_PUUID, queue=440)
    h.stub.add_match("BR1_102", seed_puuid=SEED_A_PUUID, map_id=12)
    h.stub.add_match("BR1_103", seed_puuid=SEED_A_PUUID, result="Abandoned")
    h.stub.add_match("BR1_104", seed_puuid=SEED_A_PUUID, patch="16.20")
    h.stub.add_match("BR1_105", seed_puuid="puuid-de-outra-pessoa", register_in_history=False)
    h.stub.history[SEED_A_PUUID].append("BR1_105")
    h.stub.history[SEED_A_PUUID].append("NA1_106")  # outra plataforma: nem é requisitada
    h.stub.matches["BR1_107"] = json.dumps({"metadata": {}, "sem": "info"}).encode()
    h.stub.history[SEED_A_PUUID].append("BR1_107")
    h.stub.matches["BR1_108"] = b"isto nao e json"
    h.stub.history[SEED_A_PUUID].append("BR1_108")

    report = await h.run()

    assert report.new_valid == 1
    assert dict(report.excluded) == {
        "QUEUE_OUT_OF_SCOPE": 1,
        "MAP_OUT_OF_SCOPE": 1,
        "NOT_GAME_COMPLETE": 1,
        "PATCH_OUT_OF_SCOPE": 1,
        "SEED_NOT_IN_MATCH": 1,
        "PLATFORM_OUT_OF_SCOPE": 1,
        "MATCH_DOCUMENT_INVALID": 1,
        "SCHEMA_UNSUPPORTED": 1,
    }
    assert h.stub.count("timeline") == 1  # a timeline só é buscada para quem passou
    assert h.stub.count("match", "NA1_106") == 0  # plataforma errada custa zero
    for excluded_id in ("BR1_101", "BR1_102", "BR1_103", "BR1_104", "BR1_105", "BR1_107"):
        assert h.raw_store.read_if_valid(excluded_id, "match") is None  # nada gravado
    assert h.manifest().excluded_by_reason()["QUEUE_OUT_OF_SCOPE"] == 1

    again = await h.run()  # exclusões são permanentes: não repete nenhuma requisição
    assert again.new_valid == 0 and again.duplicates == 9
    assert h.stub.count("match") == 8


async def test_not_found_is_a_permanent_exclusion_and_a_missing_timeline_stores_nothing(
    tmp_path,
) -> None:
    h = one_seed(tmp_path, ["BR1_1", "BR1_2"])
    h.stub.matches["BR1_1"] = None  # 404 na partida
    h.stub.timelines["BR1_2"] = None  # 404 na timeline

    report = await h.run()

    assert dict(report.excluded) == {"RIOT_NOT_FOUND": 2} and report.new_valid == 0
    assert h.raw_store.read_if_valid("BR1_2", "match") is None  # o corpo da partida NÃO ficou
    calls = len(h.stub.calls)
    await h.run()
    assert h.stub.count("match", "BR1_1") == 1 and len(h.stub.calls) - calls == 3


async def test_an_invalid_pair_is_excluded_and_nothing_is_stored(tmp_path) -> None:
    h = one_seed(tmp_path, ["BR1_1"])
    timeline = json.loads(h.stub.timelines["BR1_1"] or b"")
    timeline["info"]["participants"][0]["puuid"] = "puuid-diferente"  # identidade divergente
    h.stub.timelines["BR1_1"] = json.dumps(timeline).encode()

    report = await h.run()

    assert dict(report.excluded) == {"MATCH_TIMELINE_IDENTITY_MISMATCH": 1}
    assert not raw_pair_available(h.raw_store, "BR1_1")
    assert h.raw_store.read_if_valid("BR1_1", "match") is None


async def test_corrupted_local_raw_is_excluded_not_a_crash(tmp_path) -> None:
    h = one_seed(tmp_path, ["BR1_1", "BR1_2"])
    match_doc, _ = real_pair("BR1_1", seed_puuid=SEED_A_PUUID)
    body = json.dumps(match_doc).encode()
    import hashlib

    h.raw_store.store("BR1_1", "match", body, hashlib.sha256(body).hexdigest())
    (tmp_path / "raw" / "blobs" / f"{hashlib.sha256(body).hexdigest()}.json").write_bytes(
        b"adulterado"
    )

    report = await h.run()

    assert dict(report.excluded) == {"RAW_STORE_CORRUPTED": 1} and report.new_valid == 1


async def test_a_seed_whose_account_is_not_found_is_skipped_and_the_others_continue(
    tmp_path,
) -> None:
    h = two_seeds(tmp_path)
    del h.stub.accounts[NAME_A]  # 404 na resolução da conta A
    h.stub.add_match("BR1_2001", seed_puuid=SEED_B_PUUID)

    report = await h.run()

    assert report.stop_reason == StopReason.PARTIAL
    assert report.per_seed["seed-01"].error == "SEED_ACCOUNT_NOT_FOUND"
    assert report.per_seed["seed-02"].new_valid == 1


# ---------------------------------------------------------------------------
# segurança: segredo, lock, credencial
# ---------------------------------------------------------------------------


async def test_real_collection_without_the_hash_secret_fails_before_any_request(tmp_path) -> None:
    h = one_seed(tmp_path, ["BR1_1"])

    for secret in (None, ""):
        with pytest.raises(SubjectHashSecretMissingError):
            await h.run(secret=secret)

    assert h.stub.calls == []  # nenhuma requisição
    assert not (tmp_path / "collection" / "manifest.json").exists()  # nem o manifesto foi tocado


async def test_two_collections_cannot_share_a_manifest(tmp_path) -> None:
    h = one_seed(tmp_path, ["BR1_1"])

    with h.manifest().exclusive():
        with pytest.raises(ManifestLockedError):
            await h.run()

    assert h.stub.calls == []


async def test_401_stops_safely_blocks_until_the_key_is_confirmed_and_resumes_without_duplicates(
    tmp_path,
) -> None:
    h = one_seed(tmp_path, ["BR1_1", "BR1_2", "BR1_3", "BR1_4"])
    h.stub.fail_auth_after(5)  # conta, lista, partida 1, timeline 1, partida 2... 401 na timeline 2

    first = await h.run()

    assert first.stop_reason == StopReason.AUTH_FAILED and first.new_valid == 1
    assert h.manifest().auth_blocked_at is not None
    assert h.manifest().cursor("seed-01").exhausted is False  # a partida em voo será refeita
    calls_after_first = len(h.stub.calls)

    blocked = await h.run()  # sem confirmar a troca da chave: recusa antes de qualquer requisição
    assert blocked.stop_reason == StopReason.AUTH_BLOCKED and len(h.stub.calls) == calls_after_first

    still_bad = await h.run(resume=True)  # confirmou, mas a chave continua inválida
    assert still_bad.stop_reason == StopReason.AUTH_FAILED
    assert len(h.stub.calls) == calls_after_first + 1  # custou UMA requisição e bloqueou de novo
    assert h.manifest().auth_blocked_at is not None

    h.stub.fix_auth()  # troca manual da chave
    resumed = await h.run(resume=True)

    assert resumed.stop_reason == StopReason.COMPLETED and resumed.new_valid == 3
    assert h.manifest().auth_blocked_at is None
    assert h.manifest().fetched_ids() == ["BR1_1", "BR1_2", "BR1_3", "BR1_4"]  # sem duplicar
    assert h.stub.count("timeline", "BR1_1") == 1  # a já coletada nunca foi refeita
    assert h.manifest().requests_used_total == len(h.stub.calls)  # toda tentativa contou


async def test_403_is_treated_like_401(tmp_path) -> None:
    h = one_seed(tmp_path, ["BR1_1"])
    h.stub.script("account", status_response(403))

    report = await h.run()

    assert report.stop_reason == StopReason.AUTH_FAILED and h.manifest().auth_blocked_at is not None


# ---------------------------------------------------------------------------
# 429 / 5xx
# ---------------------------------------------------------------------------


async def test_429_with_a_long_retry_after_persists_a_cooldown_that_later_runs_respect(
    tmp_path,
) -> None:
    h = one_seed(tmp_path, ["BR1_1", "BR1_2"])
    h.stub.script("match:BR1_1", status_response(429, Retry_After="900"))

    first = await h.run()

    assert first.stop_reason == StopReason.RATE_LIMITED
    cooldown = h.manifest().cooldown_until
    assert cooldown is not None and 890 <= (cooldown - h.time.wall).total_seconds() <= 910
    calls = len(h.stub.calls)

    during = await h.run()  # cooldown vigente: nenhuma requisição, orçamento intacto
    assert during.stop_reason == StopReason.COOLDOWN_ACTIVE and len(h.stub.calls) == calls
    assert during.cooldown_seconds_remaining and during.cooldown_seconds_remaining > 800

    h.time.advance(901)
    after = await h.run()
    assert after.stop_reason == StopReason.COMPLETED and after.new_valid == 2


async def test_short_retry_after_is_waited_globally_and_the_retry_counts_in_the_budget(
    tmp_path,
) -> None:
    h = one_seed(tmp_path, ["BR1_1"])
    h.stub.script("match:BR1_1", status_response(429, Retry_After="7"))

    report = await h.run()

    assert report.stop_reason == StopReason.COMPLETED and report.new_valid == 1
    assert h.stub.count("match", "BR1_1") == 2 and report.requests == len(h.stub.calls)  # 429 + ok
    first_call = h.call_times[h.stub.calls.index(("match", "BR1_1"))]
    retry_call = h.call_times[
        len(h.stub.calls) - 1 - [k for k, _ in reversed(h.stub.calls)].index("match")
    ]
    assert retry_call - first_call >= 7  # a nova tentativa respeitou o Retry-After


async def test_429_without_retry_after_uses_a_conservative_cooldown(tmp_path) -> None:
    h = one_seed(tmp_path, ["BR1_1"])
    h.stub.script("list", status_response(429))

    report = await h.run()

    assert report.stop_reason == StopReason.RATE_LIMITED
    cooldown = h.manifest().cooldown_until
    assert cooldown is not None and (cooldown - h.time.wall).total_seconds() >= 100


async def test_global_limiter_paces_the_whole_run_from_learned_headers(tmp_path) -> None:
    h = one_seed(tmp_path, ["BR1_1", "BR1_2", "BR1_3"])
    h.stub.response_headers = {"X-App-Rate-Limit": "4:20", "X-App-Rate-Limit-Count": "1:20"}
    limiter = RiotRateLimiter(
        app_windows=(RateWindow(100_000, 1.0),),
        safety_margin=1.0,
        clock=h.time.monotonic,
        sleep=h.time.sleep,
    )

    report = await h.run(limiter=limiter)

    assert report.new_valid == 3 and report.limiter_wait_seconds > 0
    # nunca mais de 4 chamadas dentro de qualquer janela de 20 s
    for at in h.call_times:
        assert len([t for t in h.call_times if at - 20 < t <= at]) <= 4


async def test_transient_5xx_stalls_the_cursor_and_the_next_run_collects_it_once(tmp_path) -> None:
    h = one_seed(tmp_path, ["BR1_1", "BR1_2", "BR1_3"])
    h.stub.script(
        "match:BR1_2", *[status_response(503)] * 3
    )  # esgota as 3 tentativas do transporte

    first = await h.run()

    assert first.stop_reason == StopReason.PARTIAL and first.transient_failures == 1
    assert first.new_valid == 1 and h.manifest().status_of("BR1_2") is None  # NÃO virou exclusão
    assert h.manifest().cursor("seed-01").exhausted is False  # passada aberta: tenta de novo
    assert first.requests == len(h.stub.calls)  # cada tentativa contou

    second = await h.run()

    assert second.stop_reason == StopReason.COMPLETED and second.new_valid == 2
    assert h.manifest().fetched_ids() == ["BR1_1", "BR1_2", "BR1_3"]


async def test_a_match_that_keeps_failing_is_eventually_excluded_not_a_permanent_blocker(
    tmp_path,
) -> None:
    h = one_seed(tmp_path, ["BR1_1", "BR1_2"])
    h.stub.script("match:BR1_1", *[status_response(500)] * 9)

    for _ in range(2):  # 1ª e 2ª falha: a semente trava nela, nada é excluído nem pulado
        report = await h.run()
        assert report.stop_reason == StopReason.PARTIAL
        assert h.manifest().status_of("BR1_1") is None and h.manifest().status_of("BR1_2") is None
    third = await h.run()  # 3ª falha da MESMA partida: sai da frente e a semente segue

    assert h.manifest().excluded_by_reason() == {"UPSTREAM_PERSISTENT_ERROR": 1}
    assert third.new_valid == 1 and h.manifest().status_of("BR1_2") == "FETCHED"


async def test_consecutive_failures_across_seeds_stop_the_run_as_unstable(tmp_path) -> None:
    h = two_seeds(tmp_path)
    h.stub.add_match("BR1_1", seed_puuid=SEED_A_PUUID)
    h.stub.add_match("BR1_2", seed_puuid=SEED_B_PUUID)
    h.stub.script("match", *[status_response(502)] * 60)

    report = await h.run(ceilings=CollectionCeilings(500, 50, 300, max_consecutive_failures=2))

    assert report.stop_reason == StopReason.UPSTREAM_UNSTABLE
    assert report.transient_failures == 2 and report.new_valid == 0


# ---------------------------------------------------------------------------
# crash entre o fetch e a gravação
# ---------------------------------------------------------------------------


class _Boom(Exception):
    pass


async def test_crash_between_the_two_raw_writes_leaves_nothing_analysable_and_resumes(
    tmp_path, monkeypatch
) -> None:
    h = one_seed(tmp_path, ["BR1_1"])
    original_store = h.raw_store.store

    def crash_on_timeline(match_id: str, kind: str, body: bytes, sha256: str) -> None:
        if kind == "timeline":
            raise _Boom("queda simulada entre a partida e a timeline")
        original_store(match_id, kind, body, sha256)

    monkeypatch.setattr(h.raw_store, "store", crash_on_timeline)
    with pytest.raises(_Boom):
        await h.run()

    assert h.manifest().status_of("BR1_1") is None  # nunca marcada como coletada
    assert not raw_pair_available(h.raw_store, "BR1_1")  # par incompleto: nada importável
    assert h.raw_store.read_if_valid("BR1_1", "match") is not None  # só a partida ficou
    assert h.manifest().runs[-1]["stopReason"] == "INTERRUPTED"
    assert h.manifest().requests_used_total == len(h.stub.calls)

    monkeypatch.setattr(h.raw_store, "store", original_store)
    report = await h.run()

    assert report.new_valid == 1 and report.raw_reused == 1  # a partida foi REAPROVEITADA
    assert h.stub.count("match", "BR1_1") == 1  # nenhuma partida baixada duas vezes
    assert h.stub.count("timeline", "BR1_1") == 2  # só a timeline foi refeita
    assert raw_pair_available(h.raw_store, "BR1_1")


async def test_crash_after_both_writes_but_before_the_manifest_resumes_with_zero_requests(
    tmp_path, monkeypatch
) -> None:
    h = one_seed(tmp_path, ["BR1_1"])
    original = CollectionManifest.mark_fetched
    armed = {"on": True}

    def crash_once(self, *args, **kwargs):
        if armed["on"]:
            armed["on"] = False
            raise _Boom("queda simulada antes de marcar FETCHED")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(CollectionManifest, "mark_fetched", crash_once)
    with pytest.raises(_Boom):
        await h.run()
    assert raw_pair_available(h.raw_store, "BR1_1") and h.manifest().status_of("BR1_1") is None
    fetched_before = (h.stub.count("match"), h.stub.count("timeline"))

    report = await h.run()

    assert report.new_valid == 1 and report.raw_reused == 2
    assert (h.stub.count("match"), h.stub.count("timeline")) == fetched_before  # 0 novas


async def test_a_page_that_repeats_an_id_counts_the_second_as_duplicate(tmp_path) -> None:
    h = one_seed(tmp_path, ["BR1_1", "BR1_2"])
    h.stub.history[SEED_A_PUUID] = ["BR1_1", "BR1_1", "BR1_2"]

    report = await h.run()

    assert (report.new_valid, report.duplicates) == (2, 1)
    assert h.stub.count("match", "BR1_1") == 1


async def test_time_window_is_sent_to_the_listing_only_when_requested(tmp_path) -> None:
    h = one_seed(tmp_path, ["BR1_1"])

    await h.run(start_time=1_780_000_000, end_time=1_790_000_000)
    await h.run()

    assert h.stub.list_params[0]["startTime"] == "1780000000"
    assert h.stub.list_params[0]["endTime"] == "1790000000"
    assert "startTime" not in h.stub.list_params[-1]
    assert all(p["queue"] == "420" for p in h.stub.list_params)


async def test_solo_duo_and_flex_are_listed_as_ranked_and_both_stored(tmp_path) -> None:
    h = one_seed(tmp_path, ["BR1_1"])
    h.filters = MatchFilterConfig(frozenset({420, 440}), 11, h.filters.patches)
    h.stub.add_match("BR1_2", seed_puuid=SEED_A_PUUID, queue=440)
    h.stub.add_match("BR1_3", seed_puuid=SEED_A_PUUID, queue=450)

    report = await h.run()

    assert report.new_valid == 2
    assert set(h.manifest().fetched_ids()) == {"BR1_1", "BR1_2"}
    assert all(p["type"] == "ranked" and "queue" not in p for p in h.stub.list_params)


# ---------------------------------------------------------------------------
# relatório e dado pessoal
# ---------------------------------------------------------------------------


async def test_reports_and_manifest_carry_no_personal_data(tmp_path, caplog) -> None:
    caplog.set_level(logging.DEBUG)
    h = two_seeds(tmp_path)
    for match_id in ("BR1_1001", "BR1_1002"):
        h.stub.add_match(match_id, seed_puuid=SEED_A_PUUID)
    h.stub.add_match("BR1_2001", seed_puuid=SEED_B_PUUID, queue=440)  # perda por filtro
    h.stub.add_match("BR1_2002", seed_puuid=SEED_B_PUUID)
    h.stub.script("match:BR1_1002", status_response(429, Retry_After="3"))

    report = await h.run()
    await h.run()
    data = build_collection_report(h.manifest())
    outputs = [
        (h.directory / "collection" / "manifest.json").read_text(),
        json.dumps(report.as_dict()),
        json.dumps(data),
        render_collection_report(data),
        caplog.text,
        repr(h.seeds),
    ]
    derived = [
        compute_real_subject_hash(puuid, HASH_SECRET)
        for puuid in (SEED_A_PUUID, SEED_B_PUUID, "puuid-outro-BR1_1001")
    ]

    for text in outputs:
        for sentinel in (*PERSONAL_SENTINELS, *derived, "puuid-outro"):
            assert sentinel not in text, sentinel
    # e o relatório diz o essencial
    assert data["totals"]["fetched"] == 3 and data["totals"]["excludedByReason"] == {
        "QUEUE_OUT_OF_SCOPE": 1
    }
    assert data["run"]["stopReason"] == "COMPLETED"
    assert set(data["resume"]["seeds"]) == {"seed-01", "seed-02"}


async def test_report_selects_a_run_and_survives_an_empty_manifest(tmp_path) -> None:
    h = one_seed(tmp_path, ["BR1_1"])
    empty = build_collection_report(h.manifest())
    assert empty["run"] is None and "nenhuma rodada" in render_collection_report(empty)

    await h.run()
    await h.run()

    first = build_collection_report(h.manifest(), run=1)["run"]
    last = build_collection_report(h.manifest())["run"]
    assert first["newValid"] == 1 and last["newValid"] == 0 and last["duplicates"] == 1


async def test_the_run_report_records_the_real_behaviour_the_server_showed(tmp_path) -> None:
    """Rodada 2a do CC-206: o relatório mostra headers de limite, status por tipo de chamada,
    tamanho de página, janela de datas e o intervalo de datas das partidas — só agregados."""
    h = one_seed(tmp_path, ["BR1_1", "BR1_2"], start_ms=1_784_073_600_000)  # 2026-07-15
    h.stub.add_match(
        "BR1_3", seed_puuid=SEED_A_PUUID, patch="16.5", start_ms=1_780_000_000_000
    )  # fora do patch: perda por filtro com a data conhecida
    h.stub.response_headers = {
        "X-App-Rate-Limit": "20:1,100:120",
        "X-App-Rate-Limit-Count": "1:1,7:120",
        "X-Method-Rate-Limit": "2000:10",
    }
    h.stub.script("match:BR1_2", status_response(429, Retry_After="4"))

    report = await h.run(start_time=1_783_900_000, end_time=1_790_000_000)

    observed = report.as_dict()["observed"]
    assert observed["requestedPageSize"] == 100 and observed["maxListPageLength"] == 3
    assert observed["timeWindow"] == {"startTime": 1_783_900_000, "endTime": 1_790_000_000}
    assert observed["statusByKind"]["match:429"] == 1 and observed["statusByKind"]["match:200"] == 3
    limiter = observed["rateLimiter"]
    assert limiter["appWindowsLearnedFromServer"] is True
    assert limiter["appWindowsInEffect"] == [[20, 1.0], [100, 120.0]]
    assert limiter["methodWindows"]["match"] == [[2000, 10.0]]
    assert limiter["retryAfterSecondsSeen"] == [4.0]
    assert "x-app-rate-limit-count" in limiter["headerNamesSeen"]
    data = build_collection_report(h.manifest())
    assert data["totals"]["gameDates"] == {
        "fetchedByPatch": {"16.17": {"min": "2026-07-15", "max": "2026-07-15"}},
        "excludedByReason": {"PATCH_OUT_OF_SCOPE": {"min": "2026-05-28", "max": "2026-05-28"}},
    }
    text = render_collection_report(data)
    assert "observado:" in text and "Retry-After=[4.0]" in text
