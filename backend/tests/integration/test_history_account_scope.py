"""Histórico pessoal: isolamento, paginação e troca de conta com PostgreSQL real."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

from riftlab.api.app import create_app
from riftlab.config import Settings
from riftlab.ingestion.infrastructure.riot_account import RiotAccountConfig
from riftlab.ingestion.models import MatchModel, ParticipantModel
from riftlab.ranked.domain import account_key
from riftlab.ranked.models import RankedObservationModel
from riftlab.shared.db import unit_of_work


async def test_history_scope_filters_before_pagination_and_changes_with_account(
    session_factory, tmp_path
) -> None:
    token = uuid.uuid4().hex
    ids = [f"BR1_{uuid.uuid4().int % 10**14}" for _ in range(4)]
    demo = "DEMO-9897"
    account_file = tmp_path / "account.json"
    secret = "synthetic-history-secret"
    hashes = [uuid.uuid4().hex * 2, uuid.uuid4().hex * 2]
    keys = [
        account_key(RiotAccountConfig(f"synthetic-{token}-{n}", "BR1", "BR1", "AMERICAS"), secret)
        for n in range(2)
    ]

    def configure(n: int) -> None:
        account_file.write_text(
            json.dumps(
                {
                    "gameName": f"synthetic-{token}-{n}",
                    "tagLine": "BR1",
                    "platform": "BR1",
                    "regionalRouting": "AMERICAS",
                }
            )
        )

    configure(0)
    try:
        async with unit_of_work(session_factory) as session:
            now = datetime.now(UTC)
            for n, match_id in enumerate([*ids, demo]):
                session.add(
                    MatchModel(
                        match_id=match_id,
                        queue=420,
                        map_id=11,
                        patch="16.16",
                        start_at=now - timedelta(minutes=n),
                        duration_seconds=1800,
                        raw_hash=token,
                    )
                )
            await session.flush()
            for n, match_id in enumerate(ids):
                session.add(
                    ParticipantModel(
                        match_id=match_id,
                        participant_id=1,
                        champion_id=51,
                        team=100,
                        subject_hash=hashes[n % 2],
                    )
                )
            for key, subject in zip(keys, hashes, strict=True):
                session.add(
                    RankedObservationModel(
                        account_key=key,
                        subject_hash=subject,
                        queue="RANKED_SOLO_5x5",
                        season="2026-3",
                        captured_at=now,
                        state="READY",
                        origin="RIOT_LEAGUE_V4",
                    )
                )
        app = create_app(
            Settings(
                ranked_account_file=str(account_file),
                subject_hash_secret=secret,
            )
        )
        async with app.router.lifespan_context(app):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                first = await client.get("/api/v1/matches", params={"scope": "account", "limit": 1})
                assert first.status_code == 200
                page = first.json()
                assert [i["matchId"] for i in page["items"]] == [ids[0]]
                assert page["items"][0]["localParticipantId"] == 1
                assert hashes[0] not in first.text
                assert page["nextCursor"] is not None
                second = (
                    await client.get(
                        "/api/v1/matches",
                        params={
                            "scope": "account",
                            "limit": 1,
                            "cursor": page["nextCursor"],
                        },
                    )
                ).json()
                assert [i["matchId"] for i in second["items"]] == [ids[2]]
                assert second["nextCursor"] is None
                combined = (
                    await client.get(
                        "/api/v1/matches",
                        params={
                            "scope": "account_and_demo",
                            "limit": 100,
                        },
                    )
                ).json()["items"]
                assert {ids[0], ids[2], demo} <= {i["matchId"] for i in combined}
                assert not {ids[1], ids[3]} & {i["matchId"] for i in combined}
                demos = (
                    await client.get(
                        "/api/v1/matches",
                        params={
                            "scope": "demo",
                            "limit": 100,
                        },
                    )
                ).json()["items"]
                assert demo in {i["matchId"] for i in demos}
                assert all(i["matchId"].startswith("DEMO-") for i in demos)
                configure(1)
                switched = (
                    await client.get(
                        "/api/v1/matches",
                        params={
                            "scope": "account",
                            "limit": 100,
                        },
                    )
                ).json()["items"]
                assert [i["matchId"] for i in switched] == [ids[1], ids[3]]
                configure(2)  # Conta válida, mas sem observação local: nenhum fallback.
                unidentified = (
                    await client.get("/api/v1/matches", params={"scope": "account"})
                ).json()
                assert unidentified == {"items": [], "nextCursor": None}
                account_file.unlink()
                missing = (await client.get("/api/v1/matches", params={"scope": "account"})).json()
                assert missing == {"items": [], "nextCursor": None}
                assert (
                    await client.get("/api/v1/matches", params={"scope": "unknown"})
                ).status_code == 422
    finally:
        async with unit_of_work(session_factory) as session:
            await session.execute(
                delete(RankedObservationModel).where(RankedObservationModel.account_key.in_(keys))
            )
            await session.execute(
                delete(ParticipantModel).where(ParticipantModel.match_id.in_(ids))
            )
            await session.execute(delete(MatchModel).where(MatchModel.match_id.in_([*ids, demo])))
