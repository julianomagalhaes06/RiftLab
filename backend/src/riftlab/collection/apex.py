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
from collections import Counter
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from riftlab.ingestion.infrastructure.raw_store import DurableRawMatchStore
from riftlab.shared.errors import SourceError

APEX_VERSION = "apex_collection_v2"  # v2: Diamante, jogadores pulados e tier de cada partida
APEX_TIERS = ("CHALLENGER", "GRANDMASTER", "MASTER")
TIERS = (*APEX_TIERS, "DIAMOND")
DIVISIONS = ("I", "II", "III", "IV")
SOLO_QUEUE = 420
# Queda de rede (Wi-Fi, DNS): espera e tenta de novo a mesma chamada; ~22 min no total antes de
# encerrar a rodada com o manifesto parcial. Erro de chave ou de cota não entra aqui.
OUTAGE_WAITS = (30, 60, 120, 240, 300, 300, 300)
CHECKPOINT_EVERY = 25  # jogadores


@dataclass(frozen=True, slots=True)
class ApexPlan:
    patch: str
    start_time: int  # epoch em segundos do início do patch
    max_players: int
    matches_per_player: int
    max_matches: int
    tiers: tuple[str, ...] = APEX_TIERS
    skip_players: int = 0  # já listados numa rodada anterior (a ordem é a mesma)


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


async def _patient(
    call: Callable[[], Awaitable[Any]],
    counts: Counter[str],
    sleep: Callable[[float], Awaitable[None]],
    log: Any,
) -> Any:
    for wait in (*OUTAGE_WAITS, None):
        try:
            return await call()
        except SourceError as exc:
            if exc.code != "UPSTREAM_UNAVAILABLE" or wait is None:
                raise
            counts["NETWORK_WAITS"] += 1
            if log is not None:
                log(json.dumps({"rede": "indisponível", "esperaSegundos": wait}))
            await sleep(wait)
    raise AssertionError("inalcançável")


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
    """Executa a coleta e devolve o manifesto (sem identificador de jogador). Queda de rede
    longa encerra a rodada com o manifesto do que já foi gravado (`STOPPED_NETWORK`);
    `checkpoint` recebe o manifesto parcial a cada 25 jogadores."""
    counts: Counter[str] = Counter()
    started = time.monotonic()
    needed = plan.skip_players + plan.max_players
    # Ordem estável: tiers de elite pela pontuação (escala única); depois Diamante I a IV,
    # cada divisão pela pontuação. Uma rodada seguinte pula os primeiros `skip_players`.
    apex: list[tuple[int, str, str]] = []
    for tier in (t for t in plan.tiers if t in APEX_TIERS):
        entries = await _patient(
            functools.partial(transport.fetch_apex_league, tier), counts, sleep, log
        )
        counts[f"LADDER_{tier}"] = len(entries)
        apex.extend(
            (int(e.get("leaguePoints") or 0), str(e["puuid"]), tier)
            for e in entries
            if isinstance(e.get("puuid"), str) and e["puuid"]
        )
        if len(apex) >= needed and "DIAMOND" not in plan.tiers:
            break
    players: list[tuple[str, str]] = [(p, t) for _, p, t in sorted(apex, key=lambda x: -x[0])]
    if "DIAMOND" in plan.tiers:
        for division in DIVISIONS:
            if len(players) >= needed:
                break
            page = 1
            chunk: list[tuple[int, str]] = []
            while len(players) + len(chunk) < needed:
                entries = await _patient(
                    functools.partial(transport.fetch_league_division, "DIAMOND", division, page),
                    counts,
                    sleep,
                    log,
                )
                if not entries:
                    break
                counts[f"LADDER_DIAMOND_{division}"] += len(entries)
                chunk.extend(
                    (int(e.get("leaguePoints") or 0), str(e["puuid"]))
                    for e in entries
                    if isinstance(e.get("puuid"), str) and e["puuid"]
                )
                page += 1
            players.extend(
                (p, f"DIAMOND_{division}") for _, p in sorted(chunk, key=lambda x: -x[0])
            )
    players = players[plan.skip_players : needed]
    seen: set[str] = set(known or ())
    collected: list[str] = []
    tier_of: dict[str, str] = {}

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

    try:
        for index, (puuid, tier) in enumerate(players):
            if len(collected) >= plan.max_matches:
                break
            ids = await _patient(
                functools.partial(
                    transport.list_match_ids_by_puuid,
                    puuid,
                    queue=SOLO_QUEUE,
                    start=0,
                    count=plan.matches_per_player,
                    start_time=plan.start_time,
                ),
                counts,
                sleep,
                log,
            )
            counts["PLAYERS_LISTED"] += 1
            counts[f"PLAYERS_{tier}"] += 1
            for match_id in ids:
                if len(collected) >= plan.max_matches:
                    break
                if match_id in seen:
                    counts["DUPLICATE"] += 1
                    continue
                match = await _patient(
                    functools.partial(transport.fetch_match, match_id), counts, sleep, log
                )
                seen.add(match_id)
                if _patch_of(match.body) != plan.patch:
                    counts["OTHER_PATCH"] += 1
                    continue
                timeline = await _patient(
                    functools.partial(transport.fetch_timeline, match_id), counts, sleep, log
                )
                store.store(match_id, "match", match.body, match.sha256)
                store.store(match_id, "timeline", timeline.body, timeline.sha256)
                collected.append(match_id)
                tier_of[match_id] = tier
                counts["MATCHES_STORED"] += 1
                counts[f"MATCHES_{tier}"] += 1
            if (index + 1) % CHECKPOINT_EVERY == 0:
                if checkpoint is not None:
                    checkpoint(manifest())
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
    except SourceError as exc:
        if exc.code != "UPSTREAM_UNAVAILABLE":
            raise
        counts["STOPPED_NETWORK"] = 1  # queda longa: encerra com o que já foi gravado
    return manifest()


def known_match_ids(roots: Sequence[Path]) -> set[str]:
    out: set[str] = set()
    for root in roots:
        for ref in root.rglob("*.match.ref"):
            out.add(ref.name.removesuffix(".match.ref"))
    return out


__all__ = ["APEX_VERSION", "ApexPlan", "collect_apex", "known_match_ids", "split_of"]
