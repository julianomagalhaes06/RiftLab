"""Limitação GLOBAL das chamadas à Riot API (CC-206). Um único limitador para todas
as chamadas de uma execução (conta, listagem, partida, timeline): nenhuma chamada
escapa da janela só por ser de outro tipo.

O que respeita:
- janelas de APLICAÇÃO (`X-App-Rate-Limit`, ex.: "20:1,100:120") e de MÉTODO
  (`X-Method-Rate-Limit`), aprendidas das respostas reais; enquanto nenhuma resposta
  chegou, vale um padrão local CONSERVADOR (os limites da chave Development
  documentados no portal, ver CC-105) — o padrão nunca é tratado como verdade sobre a
  chave: qualquer header recebido o substitui;
- a contagem que o servidor informa (`...-Count`): se ela já encheu uma janela, outro
  consumidor da mesma chave gastou a cota e o limitador espera a janela inteira;
- `Retry-After` de um 429 (`penalize`): vale para TODAS as chamadas seguintes, não só
  para a que recebeu o 429;
- uma margem de segurança sobre cada limite (nunca usa 100% da janela).

Nunca dorme além do que o chamador permite (`max_wait_seconds`): uma espera que
estouraria o orçamento de tempo vira SourceRateLimitedError com `retry_after_seconds`,
ou seja, parada RETOMÁVEL, nunca bloqueio nem contorno. Relógio e sleep são
injetáveis para teste determinístico (nenhum teste espera de verdade)."""

from __future__ import annotations

import asyncio
import math
import time
from collections import deque
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from riftlab.shared.errors import SourceRateLimitedError
from riftlab.shared.numeric import is_ascii_digits

DEFAULT_SAFETY_MARGIN = 0.9
_EPSILON_SECONDS = 0.001


@dataclass(frozen=True, slots=True)
class RateWindow:
    """`limit` requisições em `seconds` segundos."""

    limit: int
    seconds: float

    def effective_limit(self, margin: float) -> int:
        return max(1, math.floor(self.limit * margin))


# Padrão local conservador ATÉ o primeiro header real (chave Development, 20/1 s e
# 100/2 min por região, segundo o portal em 14/09/2026 — ver CC-105). Não é verdade
# sobre a chave; é só o que se usa antes de o servidor informar as janelas.
DEFAULT_APP_WINDOWS = (RateWindow(20, 1.0), RateWindow(100, 120.0))


def parse_rate_limit_header(value: str | None) -> tuple[RateWindow, ...]:
    """ "20:1,100:120" -> (RateWindow(20, 1.0), RateWindow(100, 120.0)). Valor ausente
    ou malformado devolve () — nunca lança nem inventa uma janela."""
    windows: list[RateWindow] = []
    for part in (value or "").split(","):
        limit_text, _, seconds_text = part.strip().partition(":")
        if not (is_ascii_digits(limit_text) and is_ascii_digits(seconds_text)):
            continue
        limit, seconds = int(limit_text), int(seconds_text)
        if limit > 0 and seconds > 0:
            windows.append(RateWindow(limit, float(seconds)))
    return tuple(windows)


def parse_rate_count_header(value: str | None) -> dict[float, int]:
    """ "3:1,50:120" (contagem:janela) -> {1.0: 3, 120.0: 50}."""
    counts: dict[float, int] = {}
    for part in (value or "").split(","):
        count_text, _, seconds_text = part.strip().partition(":")
        if is_ascii_digits(count_text) and is_ascii_digits(seconds_text) and int(seconds_text) > 0:
            counts[float(int(seconds_text))] = int(count_text)
    return counts


class _Scope:
    """Histórico de envios de um escopo (aplicação ou um método) + as janelas que se
    conhece dele. O histórico é guardado SEMPRE, mesmo antes de qualquer janela ser
    aprendida: envios anteriores ao primeiro header continuam contando."""

    def __init__(self, windows: tuple[RateWindow, ...] = ()) -> None:
        self.windows = windows
        self._sent: deque[float] = deque()

    def replace(self, windows: tuple[RateWindow, ...]) -> None:
        self.windows = windows

    def _prune(self, now: float) -> None:
        horizon = max((w.seconds for w in self.windows), default=0.0)
        while self._sent and self._sent[0] <= now - max(horizon, 1.0):
            self._sent.popleft()

    def _in_window(self, window: RateWindow, now: float) -> list[float]:
        return [stamp for stamp in self._sent if stamp > now - window.seconds]

    def wait_needed(self, now: float, margin: float) -> float:
        self._prune(now)
        wait = 0.0
        for window in self.windows:
            recent = self._in_window(window, now)
            effective = window.effective_limit(margin)
            if len(recent) >= effective:
                # a janela só reabre quando o envio mais antigo que a segura expirar
                blocker = recent[len(recent) - effective]
                wait = max(wait, blocker + window.seconds - now)
        return wait

    def reserve(self, now: float) -> None:
        self._sent.append(now)

    def saturate_from_counts(self, counts: dict[float, int], now: float, margin: float) -> None:
        """A contagem do servidor já alcançou o limite efetivo: considera a janela cheia
        agora (espera a janela inteira, o pior caso seguro)."""
        for window in self.windows:
            if counts.get(window.seconds, 0) >= window.effective_limit(margin):
                while len(self._in_window(window, now)) < window.effective_limit(margin):
                    self._sent.append(now)


class RiotRateLimiter:
    def __init__(
        self,
        *,
        app_windows: tuple[RateWindow, ...] = DEFAULT_APP_WINDOWS,
        safety_margin: float = DEFAULT_SAFETY_MARGIN,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if not 0 < safety_margin <= 1:
            raise ValueError("safety_margin deve estar em (0, 1]")
        self._margin = safety_margin
        self._clock = clock
        self._sleep = sleep
        self._app = _Scope(app_windows)
        self._methods: dict[str, _Scope] = {}
        self._blocked_until = 0.0
        self._waited_seconds = 0.0
        # Observações do comportamento REAL (CC-206, rodada 2a): só nomes de header de
        # limite, janelas, contagens e Retry-After — nenhum valor pessoal.
        self._header_names: set[str] = set()
        self._app_learned = False
        self._retry_after_seen: list[float] = []
        self._last_app_count: dict[float, int] = {}

    @property
    def waited_seconds(self) -> float:
        """Tempo total dormido pelo limitador (para o relatório da rodada)."""
        return self._waited_seconds

    async def acquire(self, method_key: str, *, max_wait_seconds: float | None = None) -> None:
        """Reserva uma vaga (esperando o necessário). `max_wait_seconds` é o que o
        chamador ainda pode gastar: se a espera exigida for maior, levanta
        SourceRateLimitedError (parada retomável) em vez de dormir."""
        while True:
            now = self._clock()
            wait = max(0.0, self._blocked_until - now)
            wait = max(wait, self._app.wait_needed(now, self._margin))
            method = self._methods.setdefault(method_key, _Scope())
            wait = max(wait, method.wait_needed(now, self._margin))
            if wait <= 0:
                self._app.reserve(now)
                method.reserve(now)
                return
            wait += _EPSILON_SECONDS
            if max_wait_seconds is not None and wait > max_wait_seconds:
                raise SourceRateLimitedError(
                    code="RIOT_RATE_LIMITED",
                    detail="espera exigida pela janela de limite excederia o orçamento local; "
                    "encerrando retomável",
                    retry_after_seconds=wait,
                )
            self._waited_seconds += wait
            await self._sleep(wait)

    def observe(self, method_key: str, headers: Mapping[str, str]) -> None:
        """Aprende janelas e contagens dos headers de uma resposta real."""
        lowered = {key.lower(): value for key, value in headers.items()}
        now = self._clock()
        self._header_names |= {
            key
            for key in lowered
            if key == "retry-after" or (key.startswith("x-") and "rate" in key)
        }
        retry_after = lowered.get("retry-after")
        if retry_after is not None and is_ascii_digits(retry_after):
            self._retry_after_seen.append(float(retry_after))
        app_windows = parse_rate_limit_header(lowered.get("x-app-rate-limit"))
        if app_windows:
            self._app.replace(app_windows)
            self._app_learned = True
        app_counts = parse_rate_count_header(lowered.get("x-app-rate-limit-count"))
        if app_counts:
            self._last_app_count = app_counts
        self._app.saturate_from_counts(app_counts, now, self._margin)
        method_windows = parse_rate_limit_header(lowered.get("x-method-rate-limit"))
        if method_windows:
            method = self._methods.setdefault(method_key, _Scope())
            method.replace(method_windows)
            method.saturate_from_counts(
                parse_rate_count_header(lowered.get("x-method-rate-limit-count")),
                now,
                self._margin,
            )

    def snapshot(self) -> dict[str, Any]:
        """O que este limitador viu e aprendeu (para o relatório da rodada)."""
        return {
            "headerNamesSeen": sorted(self._header_names),
            "appWindowsInEffect": [[w.limit, w.seconds] for w in self._app.windows],
            "appWindowsLearnedFromServer": self._app_learned,
            "lastAppCount": {str(int(s)): c for s, c in sorted(self._last_app_count.items())},
            "methodWindows": {
                key: [[w.limit, w.seconds] for w in scope.windows]
                for key, scope in sorted(self._methods.items())
                if scope.windows
            },
            "retryAfterSecondsSeen": self._retry_after_seen,
            "waitedSeconds": round(self._waited_seconds, 3),
        }

    def penalize(self, seconds: float) -> None:
        """429 com Retry-After: NENHUMA chamada (de qualquer tipo) sai antes disso."""
        if seconds > 0:
            self._blocked_until = max(self._blocked_until, self._clock() + seconds)
