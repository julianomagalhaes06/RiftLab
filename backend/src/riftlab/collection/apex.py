"""Coleta de elo alto do patch vigente (CC-1506). Rede só com chave válida e teto explícito.

Sementes: a escada pública de Desafiante, Grão-Mestre e Mestre da Solo/Duo no BR1, da maior para
a menor pontuação. Para cada jogador, as partidas Solo/Duo desde o início do patch; cada partida
nova é baixada uma vez (partida + linha do tempo) e gravada na mesma loja bruta do corpus. Como
o pareamento junta jogadores de elo parecido, os dez participantes dessas partidas formam a
referência de elo alto.

Privacidade: o PUUID das sementes só existe em memória durante a execução; o manifesto guarda
ids de partida, contagens e a divisão treino/validação/teste, nunca identificador de jogador.
"""

from __future__ import annotations

import asyncio
import functools
import hashlib
import json
import time
import traceback
from collections import Counter
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from riftlab.ingestion.infrastructure.raw_store import DurableRawMatchStore
from riftlab.shared.errors import SourceAuthError, SourceError, SourceRateLimitedError

APEX_VERSION = "apex_collection_v2"  # v2: Diamante, jogadores pulados e tier de cada partida
APEX_TIERS = ("CHALLENGER", "GRANDMASTER", "MASTER")
TIERS = (*APEX_TIERS, "DIAMOND")
DIVISIONS = ("I", "II", "III", "IV")
SOLO_QUEUE = 420
# A listagem por PUUID (americas) traz partidas de qualquer servidor das Américas: uma conta
# transferida para o BR1 tem histórico em LA1/NA1 etc. O corpus é só do BR1.
PLATFORM_PREFIX = "BR1_"
# Queda de rede (Wi-Fi, DNS) ou limite de taxa: espera e tenta de novo a mesma chamada; ~22 min
# no total antes de encerrar a rodada com o manifesto do que já foi gravado.
OUTAGE_WAITS = (30, 60, 120, 240, 300, 300, 300)
CHECKPOINT_EVERY = 25  # jogadores
# Erro de um jogador ou de uma partida pula só aquele item. Muitos seguidos indicam algo
# sistemático (região errada, defeito): encerra em vez de gastar o orçamento à toa.
MAX_CONSECUTIVE_FAILURES = 25


@dataclass(frozen=True, slots=True)
class ApexPlan:
    patch: str
    start_time: int  # epoch em segundos do início do patch
    max_players: int
    matches_per_player: int
    max_matches: int
    tiers: tuple[str, ...] = APEX_TIERS
    skip_players: int = 0  # já listados numa rodada anterior (a ordem é a mesma)


class _Stop(Exception):
    """Encerra a rodada com o manifesto do que já foi gravado; o motivo vai em `counts`."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def split_of(match_id: str) -> str:
    """Divisão determinística por partida (80/10/10), a mesma em qualquer reexecução."""
    bucket = int(hashlib.sha256(f"apex:{match_id}".encode()).hexdigest()[:8], 16) % 100
    return "train" if bucket < 80 else "validation" if bucket < 90 else "test"


def _patch_of(match_body: bytes) -> str | None:
    try:
        info = json.loads(match_body).get("info") or {}
    except ValueError:
        return None
    version = str(info.get("gameVersion") or "")
    parts = version.split(".")
    return f"{parts[0]}.{parts[1]}" if len(parts) >= 2 else None


def _reason(exc: BaseException) -> str:
    """Código estável do erro, ou tipo e linha do nosso código. Nunca a mensagem: ela pode
    trazer a URL da chamada, com PUUID."""
    if isinstance(exc, SourceError):
        return exc.code
    frames = traceback.extract_tb(exc.__traceback__)
    if not frames:
        return type(exc).__name__
    return f"{type(exc).__name__} em {Path(frames[-1].filename).name}:{frames[-1].lineno}"


async def _patient(
    call: Callable[[], Awaitable[Any]],
    *,
    counts: Counter[str],
    sleep: Callable[[float], Awaitable[None]],
    log: Any,
    budget_hit: list[str],
) -> Any:
    """Rede e limite de taxa: espera e repete a chamada. Chave recusada, orçamento local
    esgotado ou espera longa demais encerram a rodada (`_Stop`). Erro da própria chamada (404,
    formato inesperado) sobe para quem decide pular o item."""
    for wait in (*OUTAGE_WAITS, None):
        try:
            return await call()
        except SourceAuthError as exc:
            raise _Stop("STOPPED_AUTH") from exc  # chave expirada ou recusada
        except SourceError as exc:
            if budget_hit:
                raise _Stop(f"STOPPED_{budget_hit[0]}") from exc
            limited = isinstance(exc, SourceRateLimitedError)
            if not limited and exc.code != "UPSTREAM_UNAVAILABLE":
                raise
            if wait is None:
                raise _Stop("STOPPED_RATE_LIMIT" if limited else "STOPPED_NETWORK") from exc
            seconds = float(wait)
            if isinstance(exc, SourceRateLimitedError) and exc.retry_after_seconds:
                seconds = max(seconds, exc.retry_after_seconds)
            counts["RATE_LIMIT_WAITS" if limited else "NETWORK_WAITS"] += 1
            if log is not None:
                state = "limite de taxa" if limited else "indisponível"
                log(json.dumps({"rede": state, "esperaSegundos": seconds}))
            await sleep(seconds)
    raise AssertionError("inalcançável")


async def _seed_players(
    transport: Any,
    plan: ApexPlan,
    counts: Counter[str],
    patient: Callable[[Callable[[], Awaitable[Any]]], Awaitable[Any]],
) -> list[tuple[str, str]]:
    """Sementes em ordem estável: tiers de elite pela pontuação (escala única); depois o
    Diamante, com as divisões I a IV intercaladas (cada uma pela pontuação). Uma escada que
    falha (formato inesperado) fica de fora; as outras seguem."""
    needed = plan.skip_players + plan.max_players
    apex: list[tuple[int, str, str]] = []
    for tier in (t for t in plan.tiers if t in APEX_TIERS):
        try:
            entries = await patient(functools.partial(transport.fetch_apex_league, tier))
        except SourceError as exc:
            counts[f"LADDER_FAILED_{tier}_{exc.code}"] += 1
            continue
        counts[f"LADDER_{tier}"] = len(entries)
        apex.extend(
            (int(e.get("leaguePoints") or 0), str(e["puuid"]), tier)
            for e in entries
            if isinstance(e.get("puuid"), str) and e["puuid"]
        )
        if len(apex) >= needed and "DIAMOND" not in plan.tiers:
            break
    players: list[tuple[str, str]] = [(p, t) for _, p, t in sorted(apex, key=lambda x: -x[0])]
    if "DIAMOND" in plan.tiers and len(players) < needed:
        # Parte igual de cada divisão (I a IV), intercaladas: se o teto de partidas chegar antes,
        # as quatro divisões já estão representadas (variedade de jogadores).
        share = -(-(needed - len(players)) // len(DIVISIONS))
        by_division: list[list[tuple[str, str]]] = []
        for division in DIVISIONS:
            page = 1
            chunk: list[tuple[int, str]] = []
            while len(chunk) < share:
                try:
                    entries = await patient(
                        functools.partial(
                            transport.fetch_league_division, "DIAMOND", division, page
                        )
                    )
                except SourceError as exc:
                    counts[f"LADDER_FAILED_DIAMOND_{division}_{exc.code}"] += 1
                    break
                if not entries:
                    break
                counts[f"LADDER_DIAMOND_{division}"] += len(entries)
                chunk.extend(
                    (int(e.get("leaguePoints") or 0), str(e["puuid"]))
                    for e in entries
                    if isinstance(e.get("puuid"), str) and e["puuid"]
                )
                page += 1
            ordered = sorted(chunk, key=lambda x: -x[0])[:share]
            by_division.append([(p, f"DIAMOND_{division}") for _, p in ordered])
        for position in range(share):
            players.extend(group[position] for group in by_division if position < len(group))
    return players[plan.skip_players : needed]


async def collect_apex(
    transport: Any,
    store: DurableRawMatchStore,
    plan: ApexPlan,
    *,
    known: set[str] | None = None,
    log: Any = None,
    checkpoint: Callable[[dict[str, Any]], None] | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> dict[str, Any]:
    """Executa a coleta e devolve o manifesto (sem identificador de jogador).

    Nunca termina com exceção: erro de um jogador ou de uma partida pula o item
    (`SKIPPED_<PLAYER|MATCH>_<código>`); chave recusada, orçamento local esgotado, queda de rede
    longa, falhas seguidas ou defeito inesperado encerram a rodada com o manifesto do que já
    foi gravado (`STOPPED_<motivo>`). `checkpoint` recebe o manifesto parcial a cada 25
    jogadores."""
    counts: Counter[str] = Counter()
    started = time.monotonic()
    budget_hit: list[str] = []  # LOCAL_TIME_BUDGET / LOCAL_REQUEST_BUDGET, avisado pelo transporte
    observe = getattr(transport, "set_local_budget_observer", None)
    if observe is not None:
        observe(lambda _kind, reason: budget_hit.append(reason))
    patient = functools.partial(
        _patient, counts=counts, sleep=sleep, log=log, budget_hit=budget_hit
    )
    seen: set[str] = set(known or ())
    collected: list[str] = []
    tier_of: dict[str, str] = {}
    failures = 0  # erros seguidos de jogador ou partida

    def manifest() -> dict[str, Any]:
        splits: dict[str, list[str]] = {"train": [], "validation": [], "test": []}
        for match_id in sorted(collected):
            splits[split_of(match_id)].append(match_id)
        return {
            "schemaVersion": APEX_VERSION,
            "patch": plan.patch,
            "queue": SOLO_QUEUE,
            "tiers": list(plan.tiers),
            "skipPlayers": plan.skip_players,
            # Tier da semente que trouxe cada partida (sem identificador de jogador).
            "matchTier": dict(sorted(tier_of.items())),
            "startTime": plan.start_time,
            "requests": transport.requests_made,
            "seconds": round(time.monotonic() - started, 1),
            "counts": dict(sorted(counts.items())),
            "split": {f"{k}MatchIds": v for k, v in splits.items()},
        }

    def skip(kind: str, exc: Exception, match_id: str | None = None) -> None:
        nonlocal failures
        failures += 1
        reason = _reason(exc)
        counts[f"SKIPPED_{kind}_{reason if isinstance(exc, SourceError) else 'UNEXPECTED'}"] += 1
        if log is not None:
            log(json.dumps({"pulado": kind.lower(), "partida": match_id, "motivo": reason}))
        if failures >= MAX_CONSECUTIVE_FAILURES:
            raise _Stop("STOPPED_REPEATED_FAILURES") from exc

    try:
        players = await _seed_players(transport, plan, counts, patient)
        for index, (puuid, tier) in enumerate(players):
            if len(collected) >= plan.max_matches:
                break
            try:
                ids = await patient(
                    functools.partial(
                        transport.list_match_ids_by_puuid,
                        puuid,
                        queue=SOLO_QUEUE,
                        start=0,
                        count=plan.matches_per_player,
                        start_time=plan.start_time,
                    )
                )
            except _Stop:
                raise
            except Exception as exc:
                skip("PLAYER", exc)  # PUUID nunca vai para o log
                ids = []
            else:
                failures = 0
                counts["PLAYERS_LISTED"] += 1
                counts[f"PLAYERS_{tier}"] += 1
            for match_id in ids:
                if len(collected) >= plan.max_matches:
                    break
                if match_id in seen:
                    counts["DUPLICATE"] += 1
                    continue
                seen.add(match_id)
                if not match_id.startswith(PLATFORM_PREFIX):
                    counts["OTHER_PLATFORM"] += 1
                    continue
                try:
                    match = await patient(functools.partial(transport.fetch_match, match_id))
                    if _patch_of(match.body) != plan.patch:
                        counts["OTHER_PATCH"] += 1
                        failures = 0
                        continue
                    timeline = await patient(functools.partial(transport.fetch_timeline, match_id))
                    store.store(match_id, "match", match.body, match.sha256)
                    store.store(match_id, "timeline", timeline.body, timeline.sha256)
                except _Stop:
                    raise
                except Exception as exc:
                    skip("MATCH", exc, match_id)
                    continue
                failures = 0
                collected.append(match_id)
                tier_of[match_id] = tier
                counts["MATCHES_STORED"] += 1
                counts[f"MATCHES_{tier}"] += 1
            if (index + 1) % CHECKPOINT_EVERY == 0:
                if checkpoint is not None:
                    try:
                        checkpoint(manifest())
                    except Exception as exc:  # disco: a coleta segue, o manifesto final sai
                        counts["CHECKPOINT_FAILED"] += 1
                        if log is not None:
                            log(json.dumps({"checkpoint": _reason(exc)}))
                if log is not None:
                    log(
                        json.dumps(
                            {
                                "players": index + 1,
                                "matches": len(collected),
                                "requests": transport.requests_made,
                                "seconds": round(time.monotonic() - started),
                            }
                        )
                    )
    except _Stop as stop:
        counts[stop.reason] = 1
        if log is not None:
            log(json.dumps({"encerrado": stop.reason}))
    except Exception as exc:  # defeito fora de um item: encerra sem perder o já gravado
        counts["STOPPED_UNEXPECTED"] = 1
        if log is not None:
            log(json.dumps({"encerrado": _reason(exc)}))
    return manifest()


def known_match_ids(roots: Sequence[Path]) -> set[str]:
    out: set[str] = set()
    for root in roots:
        for ref in root.rglob("*.match.ref"):
            out.add(ref.name.removesuffix(".match.ref"))
    return out


__all__ = ["APEX_VERSION", "ApexPlan", "collect_apex", "known_match_ids", "split_of"]
