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

import hashlib
import json
import time
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from riftlab.ingestion.infrastructure.raw_store import DurableRawMatchStore

APEX_VERSION = "apex_collection_v1"
TIERS = ("CHALLENGER", "GRANDMASTER", "MASTER")
SOLO_QUEUE = 420


@dataclass(frozen=True, slots=True)
class ApexPlan:
    patch: str
    start_time: int  # epoch em segundos do início do patch
    max_players: int
    matches_per_player: int
    max_matches: int


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


async def collect_apex(
    transport: Any,
    store: DurableRawMatchStore,
    plan: ApexPlan,
    *,
    known: set[str] | None = None,
    log: Any = None,
) -> dict[str, Any]:
    """Executa a coleta e devolve o manifesto (sem identificador de jogador)."""
    counts: Counter[str] = Counter()
    started = time.monotonic()
    players: list[tuple[int, str]] = []
    for tier in TIERS:
        entries = await transport.fetch_apex_league(tier)
        counts[f"LADDER_{tier}"] = len(entries)
        players.extend(
            (int(e.get("leaguePoints") or 0), str(e["puuid"]))
            for e in entries
            if isinstance(e.get("puuid"), str) and e["puuid"]
        )
        if len(players) >= plan.max_players:
            break
    players.sort(key=lambda p: -p[0])
    seen: set[str] = set(known or ())
    collected: list[str] = []
    for index, (_, puuid) in enumerate(players[: plan.max_players]):
        if len(collected) >= plan.max_matches:
            break
        ids = await transport.list_match_ids_by_puuid(
            puuid,
            queue=SOLO_QUEUE,
            start=0,
            count=plan.matches_per_player,
            start_time=plan.start_time,
        )
        counts["PLAYERS_LISTED"] += 1
        for match_id in ids:
            if len(collected) >= plan.max_matches:
                break
            if match_id in seen:
                counts["DUPLICATE"] += 1
                continue
            seen.add(match_id)
            match = await transport.fetch_match(match_id)
            if _patch_of(match.body) != plan.patch:
                counts["OTHER_PATCH"] += 1
                continue
            timeline = await transport.fetch_timeline(match_id)
            store.store(match_id, "match", match.body, match.sha256)
            store.store(match_id, "timeline", timeline.body, timeline.sha256)
            collected.append(match_id)
            counts["MATCHES_STORED"] += 1
        if log is not None and (index + 1) % 25 == 0:
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
    splits: dict[str, list[str]] = {"train": [], "validation": [], "test": []}
    for match_id in sorted(collected):
        splits[split_of(match_id)].append(match_id)
    return {
        "schemaVersion": APEX_VERSION,
        "patch": plan.patch,
        "queue": SOLO_QUEUE,
        "tiers": list(TIERS),
        "startTime": plan.start_time,
        "requests": transport.requests_made,
        "seconds": round(time.monotonic() - started, 1),
        "counts": dict(sorted(counts.items())),
        "split": {f"{k}MatchIds": v for k, v in splits.items()},
    }


def known_match_ids(roots: Sequence[Path]) -> set[str]:
    out: set[str] = set()
    for root in roots:
        for ref in root.rglob("*.match.ref"):
            out.add(ref.name.removesuffix(".match.ref"))
    return out


__all__ = ["APEX_VERSION", "ApexPlan", "collect_apex", "known_match_ids", "split_of"]
