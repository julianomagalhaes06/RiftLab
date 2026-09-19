"""Limitador global da Riot API (CC-206): janelas deslizantes, headers de app/método,
contagem do servidor, Retry-After global e orçamento de espera. Relógio e sleep falsos:
nenhum teste espera de verdade."""

from __future__ import annotations

import pytest

from riftlab.ingestion.infrastructure.riot_rate_limiter import (
    DEFAULT_APP_WINDOWS,
    RateWindow,
    RiotRateLimiter,
    parse_rate_count_header,
    parse_rate_limit_header,
)
from riftlab.shared.errors import SourceRateLimitedError


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _limiter(clock: FakeClock, **kwargs: object) -> RiotRateLimiter:
    return RiotRateLimiter(clock=clock, sleep=clock.sleep, **kwargs)  # type: ignore[arg-type]


def test_parse_headers_are_tolerant() -> None:
    assert parse_rate_limit_header("20:1,100:120") == (RateWindow(20, 1.0), RateWindow(100, 120.0))
    assert parse_rate_limit_header(" 5:10 ") == (RateWindow(5, 10.0),)
    for bad in (None, "", "abc", "0:1", "1:0", "1:x", ":", "-1:5"):
        assert parse_rate_limit_header(bad) == ()
    assert parse_rate_count_header("3:1,50:120") == {1.0: 3, 120.0: 50}
    assert parse_rate_count_header("lixo") == {}


def test_default_windows_are_the_documented_development_key_limits() -> None:
    assert DEFAULT_APP_WINDOWS == (RateWindow(20, 1.0), RateWindow(100, 120.0))
    assert RateWindow(20, 1.0).effective_limit(0.9) == 18
    assert RateWindow(1, 1.0).effective_limit(0.5) == 1  # nunca zero


async def test_never_exceeds_the_effective_limit_inside_a_window() -> None:
    clock = FakeClock()
    limiter = _limiter(clock, app_windows=(RateWindow(10, 1.0),), safety_margin=0.5)  # 5/s

    stamps = []
    for _ in range(12):
        await limiter.acquire("match")
        stamps.append(clock.now)

    for index, stamp in enumerate(stamps):
        in_window = [s for s in stamps if stamp - 1.0 < s <= stamp]
        assert len(in_window) <= 5, (index, len(in_window))
    assert clock.sleeps  # precisou esperar
    assert limiter.waited_seconds == pytest.approx(sum(clock.sleeps))


async def test_no_wait_while_inside_every_window() -> None:
    clock = FakeClock()
    limiter = _limiter(clock, app_windows=(RateWindow(100, 60.0),))

    for _ in range(50):
        await limiter.acquire("account")

    assert clock.sleeps == []


async def test_shortest_and_longest_window_both_apply() -> None:
    clock = FakeClock()
    limiter = _limiter(
        clock, app_windows=(RateWindow(2, 1.0), RateWindow(3, 100.0)), safety_margin=1.0
    )

    for _ in range(3):
        await limiter.acquire("match")  # 3 em ≤ 1 s: a janela curta (2/1 s) obriga a esperar 1 s
    assert clock.now == pytest.approx(1001.0, abs=0.01)
    before = clock.now
    await limiter.acquire("match")  # a 4ª esbarra na janela longa (3/100 s)
    assert clock.now - before > 90


async def test_learns_app_and_method_windows_from_response_headers() -> None:
    clock = FakeClock()
    limiter = _limiter(clock, app_windows=(RateWindow(1000, 1.0),), safety_margin=1.0)
    limiter.observe("timeline", {"X-App-Rate-Limit": "2:10", "X-Method-Rate-Limit": "1:5"})

    await limiter.acquire("timeline")
    await limiter.acquire("match")  # outro método: só a janela de app (2/10 s)
    assert clock.sleeps == []
    await limiter.acquire("timeline")  # 3ª de app (2/10 s) E 2ª do método (1/5 s)
    assert clock.now - 1000.0 >= 9.9


async def test_method_window_is_per_method_key() -> None:
    clock = FakeClock()
    limiter = _limiter(clock, app_windows=(RateWindow(1000, 1.0),), safety_margin=1.0)
    limiter.observe("timeline", {"x-method-rate-limit": "1:30"})

    await limiter.acquire("timeline")
    await limiter.acquire("match")  # sem janela de método aprendida para 'match'
    await limiter.acquire("match")

    assert clock.sleeps == []


async def test_server_reported_count_that_fills_a_window_forces_the_wait() -> None:
    """Outro consumidor da mesma chave já gastou a janela: a contagem do servidor manda."""
    clock = FakeClock()
    limiter = _limiter(clock, app_windows=(RateWindow(100, 120.0),), safety_margin=1.0)
    limiter.observe("match", {"X-App-Rate-Limit": "100:120", "X-App-Rate-Limit-Count": "100:120"})

    await limiter.acquire("match")

    assert clock.now - 1000.0 >= 119.9


async def test_retry_after_penalty_blocks_every_kind_of_call() -> None:
    clock = FakeClock()
    limiter = _limiter(clock, app_windows=(RateWindow(1000, 1.0),))

    limiter.penalize(30)
    await limiter.acquire("account")  # outro tipo de chamada: também espera

    assert clock.now - 1000.0 >= 30
    limiter.penalize(0)  # penalidade nula não faz nada
    before = clock.now
    await limiter.acquire("match")
    assert clock.now == before


async def test_wait_beyond_budget_is_a_resumable_stop_never_a_sleep() -> None:
    clock = FakeClock()
    limiter = _limiter(clock, app_windows=(RateWindow(1000, 1.0),))
    limiter.penalize(600)

    with pytest.raises(SourceRateLimitedError) as excinfo:
        await limiter.acquire("match", max_wait_seconds=60)

    assert (
        excinfo.value.retry_after_seconds is not None and excinfo.value.retry_after_seconds >= 600
    )
    assert clock.sleeps == []  # não dormiu nada
    assert clock.now == 1000.0


def test_rejects_invalid_margin() -> None:
    for margin in (0, -1, 1.5):
        with pytest.raises(ValueError):
            RiotRateLimiter(safety_margin=margin)


async def test_sends_made_before_the_window_was_learned_still_count() -> None:
    """Achado do teste do transporte: o histórico de envios existe antes de qualquer
    header; a janela aprendida depois vale para o que já foi enviado."""
    clock = FakeClock()
    limiter = _limiter(clock, app_windows=(RateWindow(1000, 1.0),), safety_margin=1.0)
    await limiter.acquire("match")
    await limiter.acquire("match")
    limiter.observe("match", {"X-App-Rate-Limit": "2:10"})

    await limiter.acquire("match")  # já foram 2 nos últimos 10 s

    assert clock.now - 1000.0 >= 9.9


def test_snapshot_reports_what_the_server_really_sent_and_nothing_personal() -> None:
    clock = FakeClock()
    limiter = _limiter(clock, app_windows=(RateWindow(20, 1.0), RateWindow(100, 120.0)))
    assert limiter.snapshot()["appWindowsLearnedFromServer"] is False  # ainda o padrão local

    limiter.observe(
        "match",
        {
            "X-App-Rate-Limit": "10:5,50:60",
            "X-App-Rate-Limit-Count": "1:5,3:60",
            "X-Method-Rate-Limit": "200:10",
            "X-Rate-Limit-Type": "application",
            "Retry-After": "12",
            "Content-Type": "application/json",
            "Authorization": "nunca-deve-aparecer",
        },
    )
    snapshot = limiter.snapshot()

    assert snapshot["appWindowsInEffect"] == [[10, 5.0], [50, 60.0]]
    assert snapshot["appWindowsLearnedFromServer"] is True
    assert snapshot["lastAppCount"] == {"5": 1, "60": 3}
    assert snapshot["methodWindows"] == {"match": [[200, 10.0]]}
    assert snapshot["retryAfterSecondsSeen"] == [12.0]
    assert snapshot["headerNamesSeen"] == [
        "retry-after",
        "x-app-rate-limit",
        "x-app-rate-limit-count",
        "x-method-rate-limit",
        "x-rate-limit-type",
    ]  # só nomes de header de limite: nada de Content-Type nem Authorization
    assert "nunca-deve-aparecer" not in str(snapshot)
