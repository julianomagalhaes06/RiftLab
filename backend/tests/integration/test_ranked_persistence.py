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
from riftlab.ranked.domain import SOLO_QUEUE, account_key
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
        async with unit_of_work(session_factory) as session:
            rows = (
                await session.scalars(select(Observation).where(Observation.account_key == key))
            ).all()
            assert len(rows) == 9
            assert sum(r.queue == "RANKED_FLEX_SR" for r in rows) == 4
            assert [r.state for r in rows].count("FAILED") == 1
    finally:
        async with unit_of_work(session_factory) as session:
            await session.execute(delete(Observation).where(Observation.account_key == key))


@pytest.mark.asyncio
async def test_match_association_requires_exactly_one_own_completed_solo_match(
    session_factory, tmp_path: Path
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

        async def association() -> str:
            data = await ranked_me(session_factory, settings, now=BASE + timedelta(days=1))
            return data["change"]["ownMatchAssociation"]

        assert await association() == "NONE"
        for index, match_id in enumerate(match_ids):
            async with unit_of_work(session_factory) as session:
                session.add(
                    MatchModel(
                        match_id=match_id,
                        queue=420,
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
