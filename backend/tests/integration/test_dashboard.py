"""Painel: vínculo próprio, isolamento de origem/fila, janela e ausência factual."""

from datetime import timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete
from test_ranked_persistence import BASE, settings_for

from riftlab.api.app import create_app
from riftlab.dashboard.application import dashboard
from riftlab.ingestion.models import MatchModel, ParticipantModel
from riftlab.ranked.domain import SOLO_QUEUE
from riftlab.ranked.models import RankedObservationModel as Observation
from riftlab.shared.db import unit_of_work


@pytest.mark.asyncio
async def test_own_matches_only_ordered_by_end_and_unknown_scores(
    session_factory, tmp_path, monkeypatch
):
    settings = settings_for(tmp_path / "account.json")
    settings.catalog_mappings_path = str(tmp_path / "no-mapping.yaml")
    monkeypatch.setattr("riftlab.dashboard.application.account_key", lambda *_: "dashboard-test")
    ids = [f"BR1_309000{i}" for i in range(8)] + ["DEMO-9309"]
    async with unit_of_work(session_factory) as session:
        session.add(
            Observation(
                account_key="dashboard-test",
                subject_hash="own-hash",
                queue=SOLO_QUEUE,
                season="2026-3",
                captured_at=BASE,
                state="READY",
                origin="RIOT_LEAGUE_V4",
            )
        )
        for i, mid in enumerate(ids):
            session.add(
                MatchModel(
                    match_id=mid,
                    queue=440 if i == 6 else 420,
                    map_id=11,
                    patch="16.19",
                    start_at=BASE + timedelta(minutes=i),
                    duration_seconds=7200 if i == 0 else 1800,
                    raw_hash="a" * 64,
                )
            )
        await session.flush()
        for i, mid in enumerate(ids):
            session.add(
                ParticipantModel(
                    match_id=mid,
                    participant_id=1,
                    champion_id=51,
                    team=100,
                    role="BOTTOM",
                    subject_hash="someone-else" if i == 7 else "own-hash",
                    win=None if i == 4 else True,
                    kills=None if i == 3 else 2,
                    deaths=0,
                    assists=3,
                    total_minions_killed=100,
                    neutral_minions_killed=0,
                )
            )
            session.add(
                ParticipantModel(
                    match_id=mid,
                    participant_id=2,
                    champion_id=81,
                    team=200,
                    role="BOTTOM",
                    subject_hash="own-hash" if i == 5 else "third-party",
                    win=False,
                )
            )
    try:
        result = await dashboard(session_factory, settings, window=3, origin="REAL")
        # Duas participações com o mesmo vínculo não são contadas; fila/origem/terceiros excluídos.
        assert result.total_matches == 5
        assert result.sampled_matches == 3
        assert result.win_samples == result.kda_samples == 2
        assert result.wins == 2 and result.win_rate == 1 and result.kda == 10
        assert [m.match_id for m in result.recent_matches] == [ids[0], ids[4], ids[3]]
        assert result.distinct_champions == 1
        app = create_app(settings)
        app.state.settings = settings
        app.state.session_factory = session_factory
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/v1/dashboard")
            assert response.status_code == 200
            for value in ("own-hash", "third-party", "dashboard-test", "subjectHash", "puuid"):
                assert value not in response.text
        demo = await dashboard(session_factory, settings, window=20, origin="DEMO")
        assert demo.total_matches == 1 and demo.recent_matches[0].match_id == ids[-1]
        async with unit_of_work(session_factory) as session:
            session.add(
                Observation(
                    account_key="dashboard-test",
                    subject_hash=None,
                    queue=SOLO_QUEUE,
                    season="2026-3",
                    captured_at=BASE + timedelta(days=1),
                    state="FAILED",
                    origin="RIOT_LEAGUE_V4",
                )
            )
        unavailable = await dashboard(session_factory, settings, window=20, origin="REAL")
        assert unavailable.status == "IDENTITY_UNAVAILABLE"
        assert unavailable.win_rate is None and not unavailable.recent_matches
    finally:
        async with unit_of_work(session_factory) as session:
            await session.execute(
                delete(ParticipantModel).where(ParticipantModel.match_id.in_(ids))
            )
            await session.execute(delete(MatchModel).where(MatchModel.match_id.in_(ids)))
            await session.execute(
                delete(Observation).where(Observation.account_key == "dashboard-test")
            )


@pytest.mark.asyncio
async def test_queue_follows_latest_own_match_unless_requested(
    session_factory, tmp_path, monkeypatch
):
    """CC-904: sem fila pedida, o recorte é a fila da partida própria mais recente."""
    settings = settings_for(tmp_path / "account.json")
    settings.catalog_mappings_path = str(tmp_path / "no-mapping.yaml")
    monkeypatch.setattr("riftlab.dashboard.application.account_key", lambda *_: "dash-queue")
    plan = [("BR1_3091001", 420, 0), ("BR1_3091002", 420, 60), ("BR1_3091003", 440, 120)]
    ids = [mid for mid, _, _ in plan]
    async with unit_of_work(session_factory) as session:
        session.add(
            Observation(
                account_key="dash-queue",
                subject_hash="own-q",
                queue=SOLO_QUEUE,
                season="2026-3",
                captured_at=BASE,
                state="READY",
                origin="RIOT_LEAGUE_V4",
            )
        )
        for mid, queue, minutes in plan:
            session.add(
                MatchModel(
                    match_id=mid,
                    queue=queue,
                    map_id=11,
                    patch="16.20",
                    start_at=BASE + timedelta(minutes=minutes),
                    duration_seconds=1800,
                    raw_hash="b" * 64,
                )
            )
        await session.flush()
        for mid, _, _ in plan:
            session.add(
                ParticipantModel(
                    match_id=mid,
                    participant_id=3,
                    champion_id=64,
                    team=100,
                    role="JUNGLE",
                    subject_hash="own-q",
                    win=True,
                    kills=1,
                    deaths=1,
                    assists=1,
                )
            )
    try:
        auto = await dashboard(session_factory, settings, window=20, origin="REAL")
        assert (auto.queue, auto.queue_selection, auto.total_matches) == (440, "AUTO", 1)
        assert [(q.queue, q.total_matches) for q in auto.queues] == [(420, 2), (440, 1)]
        solo = await dashboard(session_factory, settings, window=20, origin="REAL", queue=420)
        assert (solo.queue, solo.queue_selection, solo.total_matches) == (420, "EXPLICIT", 2)
        app = create_app(settings)
        app.state.settings = settings
        app.state.session_factory = session_factory
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            flex = await client.get("/api/v1/dashboard", params={"queue": "RANKED_FLEX_SR"})
            assert flex.status_code == 200 and flex.json()["queue"] == 440
            assert flex.json()["queueSelection"] == "EXPLICIT"
            bad = await client.get("/api/v1/dashboard", params={"queue": "ARAM"})
            assert bad.status_code == 422
    finally:
        async with unit_of_work(session_factory) as session:
            await session.execute(
                delete(ParticipantModel).where(ParticipantModel.match_id.in_(ids))
            )
            await session.execute(delete(MatchModel).where(MatchModel.match_id.in_(ids)))
            await session.execute(
                delete(Observation).where(Observation.account_key == "dash-queue")
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("_demo_match", ["DEMO-0008"], indirect=True)
async def test_study_counts_are_local_separate_by_origin_and_idempotent(
    app_client, session_factory, tmp_path, _demo_match
):
    from test_analyses import _decisions
    from test_learning_attempts import _attempt

    decision = (await _decisions(app_client, session_factory, tmp_path, _demo_match))[-1]
    before = (await app_client.get("/api/v1/dashboard?origin=DEMO")).json()["progress"]
    real_before = (await app_client.get("/api/v1/dashboard?origin=REAL")).json()["progress"]
    attempt = await _attempt(app_client, decision["decisionId"], "dashboard-progress-idempotent")
    assert attempt.status_code == 201
    repeated = await _attempt(app_client, decision["decisionId"], "dashboard-progress-idempotent")
    assert repeated.status_code == 200
    attempt_id = attempt.json()["attemptId"]
    for _ in range(2):
        assert (
            await app_client.post(
                f"/api/v1/learning-attempts/{attempt_id}/reveal", json={"explicit": True}
            )
        ).status_code == 200
        assert (
            await app_client.post(
                f"/api/v1/learning-attempts/{attempt_id}/reflection",
                json={"reflection": "Revi as alternativas."},
            )
        ).status_code == 200
    response = await app_client.get("/api/v1/dashboard?origin=DEMO&window=1")
    assert response.status_code == 200
    after = response.json()["progress"]
    for field in ("attempts", "revealed", "reflections"):
        assert after[field] == before[field] + 1
    real = await app_client.get("/api/v1/dashboard?origin=REAL")
    assert real.status_code == 200 and real.json()["progress"] == real_before
