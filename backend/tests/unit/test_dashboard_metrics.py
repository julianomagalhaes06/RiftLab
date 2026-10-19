import pytest
from httpx import ASGITransport, AsyncClient

from riftlab.api.app import create_app
from riftlab.config import Settings
from riftlab.dashboard.application import summarize
from riftlab.ingestion.models import MatchModel, ParticipantModel


def row(*, win=True, kills=0, deaths=0, assists=0, champion=51):
    return MatchModel(), ParticipantModel(
        champion_id=champion, win=win, kills=kills, deaths=deaths, assists=assists
    )


def test_kda_is_ratio_of_sums_with_separate_missing_denominators():
    result = summarize(
        [
            row(kills=10, deaths=0, assists=2),
            row(win=False, kills=0, deaths=4, assists=0),
            row(win=None, kills=None, assists=30, champion=81),
        ]
    )
    assert result == {
        "sampled_matches": 3,
        "win_samples": 2,
        "wins": 1,
        "win_rate": 0.5,
        "kda_samples": 2,
        "kda": 3.0,
        "distinct_champions": 2,
    }


def test_zero_deaths_is_not_missing_and_empty_is_not_zero_performance():
    assert summarize([row(kills=2, assists=3)])["kda"] == 5
    assert summarize([row()])["kda"] == 0
    assert summarize([])["win_rate"] is None
    assert summarize([])["kda"] is None
    assert summarize([row(win=None, kills=None)])["win_samples"] == 0


@pytest.mark.asyncio
async def test_dashboard_http_validation_and_no_private_fields(monkeypatch):
    calls = []

    async def fake(factory, settings, *, window, origin, queue):
        from riftlab.dashboard.api import Dashboard

        calls.append((window, origin, queue))
        return Dashboard.model_validate(
            {
                "origin": origin,
                "status": "EMPTY",
                "window": window,
                "queue": queue or 420,
                "queueSelection": "AUTO" if queue is None else "EXPLICIT",
                "queues": [{"queue": 420, "totalMatches": 0}, {"queue": 440, "totalMatches": 0}],
                "totalMatches": 0,
                **summarize([]),
                "champions": [],
                "recentMatches": [],
                "progress": {"attempts": 0, "revealed": 0, "reflections": 0, "matches": 0},
            }
        )

    monkeypatch.setattr("riftlab.dashboard.application.dashboard", fake)
    app = create_app(Settings())
    app.state.settings = Settings()
    app.state.session_factory = None
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for query in ("window=0", "window=101", "window=x", "origin=OTHER", "queue=440"):
            assert (await client.get("/api/v1/dashboard?" + query)).status_code == 422
        response = await client.get("/api/v1/dashboard?window=10&origin=DEMO")
        assert response.status_code == 200
        assert response.json()["winRate"] is None
        flex = await client.get("/api/v1/dashboard?queue=RANKED_FLEX_SR")
        assert flex.status_code == 200 and flex.json()["queueSelection"] == "EXPLICIT"
        assert calls == [(10, "DEMO", None), (20, "REAL", 440)]
        for name in ("subject_hash", "subjectHash", "puuid", "accountKey", "gameName"):
            assert name not in response.text
