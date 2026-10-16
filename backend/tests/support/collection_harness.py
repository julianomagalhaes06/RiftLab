"""Harness compartilhado dos testes da coleta (CC-206): servidor Riot falso, relógios
falsos e uma execução completa do coletor com transporte stub. Sem rede, sem chave real."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from riftlab.collection.collector import (
    CollectionCeilings,
    CollectionRunReport,
    build_collection_transport,
    run_collection,
)
from riftlab.collection.filters import MatchFilterConfig
from riftlab.collection.manifest import CollectionManifest
from riftlab.collection.seeds import Seed
from riftlab.ingestion.infrastructure.raw_store import DurableRawMatchStore
from riftlab.ingestion.infrastructure.riot_account import RiotAccountConfig
from riftlab.ingestion.infrastructure.riot_rate_limiter import RateWindow, RiotRateLimiter
from support.riot_stub import API_KEY, HASH_SECRET, RiotStub

FILTERS = MatchFilterConfig(
    queues=frozenset({420}), map_id=11, patches=frozenset({"16.16", "16.17"})
)
SEED_A_PUUID = "puuid-sentinela-semente-A"
SEED_B_PUUID = "puuid-sentinela-semente-B"
NAME_A, TAG_A = "NomeSentinelaAlfa", "TGA"
NAME_B, TAG_B = "NomeSentinelaBravo", "TGB"
PERSONAL_SENTINELS = (
    NAME_A,
    TAG_A,
    NAME_B,
    TAG_B,
    SEED_A_PUUID,
    SEED_B_PUUID,
    API_KEY,
    HASH_SECRET,
)


class FakeTime:
    """Relógio monotônico + relógio de parede falsos; dormir avança os dois."""

    def __init__(self) -> None:
        self.mono = 1000.0
        self.wall = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)

    def monotonic(self) -> float:
        return self.mono

    def utcnow(self) -> datetime:
        return self.wall

    def advance(self, seconds: float) -> None:
        self.mono += seconds
        self.wall += timedelta(seconds=seconds)

    async def sleep(self, seconds: float) -> None:
        self.advance(seconds)


def make_seed(label: str, name: str, tag: str) -> Seed:
    return Seed(label=label, account=RiotAccountConfig(name, tag, "BR1", "AMERICAS"))


@dataclass
class Harness:
    directory: Path
    stub: RiotStub = field(default_factory=RiotStub)
    time: FakeTime = field(default_factory=FakeTime)
    seeds: list[Seed] = field(default_factory=list)
    call_times: list[float] = field(default_factory=list)
    filters: MatchFilterConfig = FILTERS

    def __post_init__(self) -> None:
        self.raw_store = DurableRawMatchStore(self.directory / "raw")

    def manifest(self) -> CollectionManifest:
        """Uma instância NOVA por execução, como em processos separados."""
        return CollectionManifest(self.directory / "collection")

    def limiter(self) -> RiotRateLimiter:
        return RiotRateLimiter(
            app_windows=(RateWindow(100_000, 1.0),),
            clock=self.time.monotonic,
            sleep=self.time.sleep,
        )

    async def run(
        self,
        *,
        ceilings: CollectionCeilings | None = None,
        resume: bool = False,
        secret: str | None = HASH_SECRET,
        limiter: RiotRateLimiter | None = None,
        **kwargs: Any,
    ) -> CollectionRunReport:
        ceilings = ceilings or CollectionCeilings(
            max_requests=500, max_matches=500, max_duration_seconds=300
        )
        limiter = limiter or self.limiter()

        def handler(request: httpx.Request) -> httpx.Response:
            self.call_times.append(self.time.mono)
            return self.stub(request)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        transport = build_collection_transport(
            API_KEY, ceilings, client=client, rate_limiter=limiter, sleep=self.time.sleep
        )
        try:
            return await run_collection(
                transport=transport,
                raw_store=self.raw_store,
                manifest=self.manifest(),
                seeds=self.seeds,
                filters=self.filters,
                subject_hash_secret=secret,
                ceilings=ceilings,
                rate_limiter=limiter,
                resume_after_auth_failure=resume,
                clock=self.time.monotonic,
                now=self.time.utcnow,
                **kwargs,
            )
        finally:
            await client.aclose()


def one_seed(tmp_path, ids: list[str], **match_overrides: Any) -> Harness:
    h = Harness(tmp_path)
    h.seeds = [make_seed("seed-01", NAME_A, TAG_A)]
    h.stub.add_account(NAME_A, SEED_A_PUUID)
    for match_id in ids:
        h.stub.add_match(match_id, seed_puuid=SEED_A_PUUID, **match_overrides)
    return h


def two_seeds(tmp_path) -> Harness:
    h = Harness(tmp_path)
    h.seeds = [make_seed("seed-01", NAME_A, TAG_A), make_seed("seed-02", NAME_B, TAG_B)]
    h.stub.add_account(NAME_A, SEED_A_PUUID)
    h.stub.add_account(NAME_B, SEED_B_PUUID)
    return h
