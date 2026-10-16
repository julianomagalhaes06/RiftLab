from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from sqlalchemy import delete, select

from riftlab.config import Settings
from riftlab.ingestion.infrastructure.riot_transport import RiotFetchBudget, RiotHttpTransport
from riftlab.ingestion.infrastructure.subject_hash import compute_real_subject_hash
from riftlab.ingestion.models import MatchModel, ParticipantModel
from riftlab.ranked.application import ranked_me, refresh_ranked
from riftlab.ranked.domain import FLEX_QUEUE, SOLO_QUEUE, RankedQueue, account_key
from riftlab.ranked.models import RankedObservationModel as Observation
from riftlab.shared.db import unit_of_work
from riftlab.shared.errors import SourceAuthError

PUUID = "synthetic-own-puuid"
BASE = datetime(2026, 9, 28, 12, tzinfo=UTC)


def settings_for(path: Path) -> Settings:
    path.write_text(
        json.dumps(
            {
                "gameName": "synthetic-owner",
                "tagLine": "BR1",
                "platform": "BR1",
                "regionalRouting": "AMERICAS",
            }
        )
    )
    return Settings(
        ranked_account_file=str(path),
        subject_hash_secret="synthetic-secret",
        riot_api_key="synthetic-key",
    )


def client_for(
    tier: str = "GOLD", division: str = "III", lp: int = 76, *, status: int = 200, solo: bool = True
) -> tuple[RiotHttpTransport, httpx.AsyncClient]:
    def responder(request: httpx.Request) -> httpx.Response:
        if request.url.host == "americas.api.riotgames.com":
            return httpx.Response(200, json={"puuid": PUUID})
        assert request.url.host == "br1.api.riotgames.com"
        assert request.url.path.endswith(PUUID)
        if status != 200:
            return httpx.Response(status)
        return httpx.Response(
            200,
            json=[
                {
                    "puuid": PUUID,
                    "queueType": SOLO_QUEUE if solo else "RANKED_FLEX_SR",
                    "tier": tier,
                    "rank": division,
                    "leaguePoints": lp,
                    "wins": 20,
                    "losses": 10,
                }
            ],
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(responder))
    return RiotHttpTransport("synthetic-key", RiotFetchBudget(0, 10), client=client), client


@pytest.mark.asyncio
async def test_immutable_snapshots_errors_account_change_and_season(
    session_factory, tmp_path: Path
) -> None:
    account_file = tmp_path / "account.json"
    settings = settings_for(account_file)
    account = json.loads(account_file.read_text())
    key = account_key(
        __import__(
            "riftlab.ingestion.infrastructure.riot_account", fromlist=["RiotAccountConfig"]
        ).RiotAccountConfig(account["gameName"], account["tagLine"], "BR1", "AMERICAS"),
        "synthetic-secret",
    )
    assert key
    try:
        for day, lp in [(0, 76), (1, 76), (2, 87)]:
            transport, client = client_for(lp=lp)
            try:
                await refresh_ranked(
                    session_factory,
                    settings,
                    "2026-3",
                    transport=transport,
                    captured_at=BASE + timedelta(days=day),
                )
            finally:
                await client.aclose()
        data = await ranked_me(session_factory, settings, now=BASE + timedelta(days=2))
        assert data["status"] == "READY"
        assert data["change"]["kind"] == "SAME_DIVISION"
        assert data["change"]["leaguePointsDelta"] == 11
        assert data["change"]["ownMatchAssociation"] == "NONE"
        transport, client = client_for(status=403)
        try:
            with pytest.raises(SourceAuthError):
                await refresh_ranked(
                    session_factory,
                    settings,
                    "2026-3",
                    transport=transport,
                    captured_at=BASE + timedelta(days=3),
                )
        finally:
            await client.aclose()
        stale = await ranked_me(session_factory, settings, now=BASE + timedelta(days=3))
        assert stale["status"] == "STALE" and stale["position"]["leaguePoints"] == 87
        settings.riot_api_key = None
        assert (await ranked_me(session_factory, settings, now=BASE + timedelta(days=3)))[
            "position"
        ]["leaguePoints"] == 87
        account_file.write_text(json.dumps({**account, "gameName": "another-synthetic-owner"}))
        assert (await ranked_me(session_factory, settings))["status"] == "UNAVAILABLE"
        assert (await ranked_me(session_factory, settings, queue=FLEX_QUEUE))[
            "status"
        ] == "UNAVAILABLE"
        account_file.write_text(json.dumps(account))
        settings.riot_api_key = "synthetic-key"
        transport, client = client_for(solo=False)
        try:
            await refresh_ranked(
                session_factory,
                settings,
                "2027",
                transport=transport,
                captured_at=BASE + timedelta(days=4),
            )
        finally:
            await client.aclose()
        assert (await ranked_me(session_factory, settings, now=BASE + timedelta(days=4)))[
            "status"
        ] == "UNRANKED"
        flex = await ranked_me(
            session_factory, settings, queue=FLEX_QUEUE, now=BASE + timedelta(days=4)
        )
        assert flex["status"] == "READY" and flex["position"]["queue"] == FLEX_QUEUE
        assert flex["position"]["season"] == "2027" and flex["change"] is None
        async with unit_of_work(session_factory) as session:
            rows = (
                await session.scalars(select(Observation).where(Observation.account_key == key))
            ).all()
            assert len(rows) == 10
            assert sum(r.queue == "RANKED_FLEX_SR" for r in rows) == 5
            assert [r.state for r in rows].count("FAILED") == 2
    finally:
        async with unit_of_work(session_factory) as session:
            await session.execute(delete(Observation).where(Observation.account_key == key))


@pytest.mark.asyncio
@pytest.mark.parametrize("queue", [SOLO_QUEUE, FLEX_QUEUE])
async def test_match_association_requires_exactly_one_own_completed_same_queue_match(
    session_factory, tmp_path: Path, queue: RankedQueue
) -> None:
    settings = settings_for(tmp_path / "account.json")
    from riftlab.ingestion.infrastructure.riot_account import load_riot_account

    key = account_key(
        load_riot_account(Path(settings.ranked_account_file)), settings.subject_hash_secret
    )
    assert key
    own_hash = compute_real_subject_hash(PUUID, settings.subject_hash_secret)
    match_ids = ["BR1_310990001", "BR1_310990002"]
    try:
        for day, lp in [(0, 10), (1, 20)]:
            transport, client = client_for(lp=lp, solo=queue == SOLO_QUEUE)
            try:
                await refresh_ranked(
                    session_factory,
                    settings,
                    "2026-3",
                    transport=transport,
                    captured_at=BASE + timedelta(days=day),
                )
            finally:
                await client.aclose()

        async def association() -> str:
            data = await ranked_me(
                session_factory, settings, queue=queue, now=BASE + timedelta(days=1)
            )
            return data["change"]["ownMatchAssociation"]

        assert await association() == "NONE"
        for index, match_id in enumerate(match_ids):
            async with unit_of_work(session_factory) as session:
                session.add(
                    MatchModel(
                        match_id=match_id,
                        queue=420 if queue == SOLO_QUEUE else 440,
                        map_id=11,
                        patch="26.1",
                        start_at=BASE + timedelta(hours=12 + index),
                        duration_seconds=1800,
                        raw_hash="0" * 64,
                    )
                )
                session.add(
                    ParticipantModel(
                        match_id=match_id,
                        participant_id=1,
                        champion_id=1,
                        team=100,
                        role="TOP",
                        subject_hash=own_hash,
                    )
                )
            assert await association() == ("ONE" if index == 0 else "MULTIPLE")
        other_queue = FLEX_QUEUE if queue == SOLO_QUEUE else SOLO_QUEUE
        other = await ranked_me(
            session_factory, settings, queue=other_queue, now=BASE + timedelta(days=1)
        )
        assert other["change"]["ownMatchAssociation"] == "NONE"
    finally:
        async with unit_of_work(session_factory) as session:
            await session.execute(
                delete(ParticipantModel).where(ParticipantModel.match_id.in_(match_ids))
            )
            await session.execute(delete(MatchModel).where(MatchModel.match_id.in_(match_ids)))
            await session.execute(delete(Observation).where(Observation.account_key == key))


@pytest.mark.asyncio
async def test_get_route_is_account_scoped_and_contains_no_identifiers(
    session_factory, tmp_path: Path
) -> None:
    from httpx import ASGITransport, AsyncClient

    from riftlab.api.app import create_app
    from riftlab.ingestion.infrastructure.riot_account import load_riot_account

    settings = settings_for(tmp_path / "account.json")
    key = account_key(
        load_riot_account(Path(settings.ranked_account_file)), settings.subject_hash_secret
    )
    assert key
    transport, upstream = client_for()
    try:
        await refresh_ranked(
            session_factory, settings, "2026-3", transport=transport, captured_at=datetime.now(UTC)
        )
        app = create_app(settings)
        async with app.router.lifespan_context(app):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get("/api/v1/ranked/me")
                assert response.status_code == 200
                assert response.json()["position"]["tier"] == "GOLD"
                assert PUUID not in response.text
                assert "synthetic-owner" not in response.text
                assert "subject_hash" not in response.text
                flex = await client.get("/api/v1/ranked/me", params={"queue": FLEX_QUEUE})
                assert flex.status_code == 200
                assert flex.json()["position"]["queue"] == FLEX_QUEUE
                assert flex.json()["status"] == "UNRANKED"
                invalid = await client.get("/api/v1/ranked/me", params={"queue": "NORMAL"})
                assert invalid.status_code == 422
                settings.ranked_account_file = str(tmp_path / "absent.json")
                absent = await client.get("/api/v1/ranked/me")
                assert absent.json()["status"] == "UNAVAILABLE"
    finally:
        await upstream.aclose()
        async with unit_of_work(session_factory) as session:
            await session.execute(delete(Observation).where(Observation.account_key == key))


@pytest.mark.asyncio
async def test_transition_correction_and_tied_observations(session_factory, tmp_path: Path) -> None:
    from riftlab.ingestion.infrastructure.riot_account import load_riot_account

    settings = settings_for(tmp_path / "account.json")
    key = account_key(
        load_riot_account(Path(settings.ranked_account_file)), settings.subject_hash_secret
    )
    assert key
    try:
        for tier, division, lp, stamp in [
            ("GOLD", "I", 98, BASE),
            ("PLATINUM", "IV", 12, BASE + timedelta(hours=1)),
        ]:
            transport, client = client_for(tier, division, lp)
            try:
                await refresh_ranked(
                    session_factory, settings, "2026-3", transport=transport, captured_at=stamp
                )
            finally:
                await client.aclose()
        promoted = await ranked_me(session_factory, settings, now=BASE + timedelta(hours=1))
        assert promoted["change"]["kind"] == "TRANSITION"
        assert promoted["change"]["leaguePointsDelta"] is None
        # Empate de instante: ordem total id descendente, reproduzível na leitura.
        async with unit_of_work(session_factory) as session:
            session.add(
                Observation(
                    account_key=key,
                    queue=SOLO_QUEUE,
                    season="2026-3",
                    captured_at=BASE + timedelta(hours=2),
                    state="READY",
                    tier="PLATINUM",
                    division="IV",
                    league_points=13,
                    wins=2,
                    losses=1,
                    origin="RIOT_LEAGUE_V4",
                )
            )
        corrected = await ranked_me(session_factory, settings, now=BASE + timedelta(hours=2))
        assert corrected["change"]["kind"] == "CORRECTION"
        assert corrected["change"]["leaguePointsDelta"] is None
    finally:
        async with unit_of_work(session_factory) as session:
            await session.execute(delete(Observation).where(Observation.account_key == key))


@pytest.mark.asyncio
async def test_demo_ranked_is_fictitious_marked_and_never_used_with_a_riot_key(
    session_factory, tmp_path: Path
) -> None:
    """CC-605: elo FICTÍCIO só na demonstração (sem chave), marcado na origem."""
    from riftlab.ranked.application import DEMO_ACCOUNT_KEY, DEMO_ORIGIN, seed_demo_ranked

    async with unit_of_work(session_factory) as session:
        await session.execute(
            delete(Observation).where(Observation.account_key == DEMO_ACCOUNT_KEY)
        )
    with_key = Settings(riot_api_key="RGAPI-fake-test-key-not-real", demo_ranked=True)
    with pytest.raises(ValueError, match="sem chave"):
        await seed_demo_ranked(session_factory, with_key)

    demo = Settings(riot_api_key=None, demo_ranked=True)
    assert await seed_demo_ranked(session_factory, demo) == 2
    assert await seed_demo_ranked(session_factory, demo) == 0  # idempotente
    shown = await ranked_me(session_factory, demo)
    assert shown["status"] == "READY" and shown["reason"] is None
    assert shown["position"]["origin"] == DEMO_ORIGIN and shown["position"]["tier"] == "SILVER"
    assert shown["change"]["leaguePointsDelta"] == 12
    flex = await ranked_me(session_factory, demo, queue=FLEX_QUEUE)
    assert flex["status"] == "UNAVAILABLE" and flex["reason"] == "NO_OBSERVATION"

    # com chave da Riot a opção é ignorada: a conta fictícia nunca aparece
    real = Settings(
        riot_api_key="RGAPI-fake-test-key-not-real",
        demo_ranked=True,
        ranked_account_file=str(tmp_path / "sem-conta.json"),
    )
    assert (await ranked_me(session_factory, real))["reason"] == "ACCOUNT_OR_SECRET_MISSING"


@pytest.mark.asyncio
async def test_queue_history_deltas_failures_and_accounts_are_isolated(
    session_factory, tmp_path: Path
) -> None:
    from riftlab.ingestion.infrastructure.riot_account import load_riot_account

    settings = settings_for(tmp_path / "account.json")
    key = account_key(
        load_riot_account(Path(settings.ranked_account_file)), settings.subject_hash_secret
    )
    assert key
    try:
        async with unit_of_work(session_factory) as session:
            for queue, season, tier, points in (
                (SOLO_QUEUE, "2026", "GOLD", (10, 30)),
                (FLEX_QUEUE, "2026-flex", "PLATINUM", (50, 65)),
            ):
                for hour, value in enumerate(points):
                    session.add(
                        Observation(
                            account_key=key,
                            queue=queue,
                            season=season,
                            captured_at=BASE + timedelta(hours=hour),
                            state="READY",
                            tier=tier,
                            division="III",
                            league_points=value,
                            wins=20 + hour,
                            losses=10,
                            origin="RIOT_LEAGUE_V4",
                        )
                    )
        solo = await ranked_me(session_factory, settings, now=BASE + timedelta(hours=2))
        flex = await ranked_me(
            session_factory, settings, queue=FLEX_QUEUE, now=BASE + timedelta(hours=2)
        )
        assert solo["position"]["leaguePoints"] == 30
        assert solo["change"]["leaguePointsDelta"] == 20
        assert flex["position"]["leaguePoints"] == 65
        assert flex["change"]["leaguePointsDelta"] == 15
        assert flex["position"]["tier"] == "PLATINUM"
        async with unit_of_work(session_factory) as session:
            session.add(
                Observation(
                    account_key=key,
                    queue=FLEX_QUEUE,
                    season="2026-flex",
                    captured_at=BASE + timedelta(hours=3),
                    state="FAILED",
                    error_code="RIOT_AUTH_FAILED",
                    origin="RIOT_LEAGUE_V4",
                )
            )
        flex = await ranked_me(
            session_factory, settings, queue=FLEX_QUEUE, now=BASE + timedelta(hours=3)
        )
        assert flex["status"] == "STALE" and flex["reason"] == "RIOT_AUTH_FAILED"
        assert flex["position"]["leaguePoints"] == 65
        assert (await ranked_me(session_factory, settings, now=BASE + timedelta(hours=3)))[
            "status"
        ] == "READY"
        settings.ranked_account_file = str(tmp_path / "another.json")
        assert (await ranked_me(session_factory, settings, queue=FLEX_QUEUE))[
            "status"
        ] == "UNAVAILABLE"
    finally:
        async with unit_of_work(session_factory) as session:
            await session.execute(delete(Observation).where(Observation.account_key == key))
