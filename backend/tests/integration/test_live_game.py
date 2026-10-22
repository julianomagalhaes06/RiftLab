"""CC-1211: partida ao vivo por Riot ID (transporte Riot simulado, sem rede)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from sqlalchemy import func, select

from riftlab.config import Settings
from riftlab.ingestion.infrastructure.riot_transport import RiotFetchBudget, RiotHttpTransport
from riftlab.players import live
from riftlab.players.live import LIVE_MAX_REQUESTS, live_game, live_status
from riftlab.players.models import PlayerProfileModel as Profile
from riftlab.ranked.models import RankedObservationModel as Observation
from riftlab.shared.errors import PlayerLookupError

SECRET = "synthetic-secret"
NOW = datetime(2026, 10, 22, 15, tzinfo=UTC)
ME = "synthetic-live-puuid-00"


def _participant(n: int, **extra):
    return {
        "puuid": f"synthetic-live-puuid-{n:02d}",
        "teamId": 100 if n < 5 else 200,
        "championId": 51 + n,
        "spell1Id": 4,
        "spell2Id": 7,
        "riotId": f"Synthetic {n}#BR{n}",
        "bot": False,
        "perks": {"perkIds": [8005, 9111], "perkStyle": 8000, "perkSubStyle": 8400},
        **extra,
    }


GAME = {
    "gameId": 1,
    "mapId": 11,
    "gameMode": "CLASSIC",
    "gameQueueConfigId": 440,
    "gameStartTime": int(NOW.timestamp() * 1000) - 600_000,
    "gameLength": 600,
    "platformId": "BR1",
    "bannedChampions": [
        {"championId": 99, "teamId": 100, "pickTurn": 2},
        {"championId": 98, "teamId": 100, "pickTurn": 1},
        {"championId": -1, "teamId": 200, "pickTurn": 6},
        {"championId": 97, "teamId": 200, "pickTurn": 7},
    ],
    "participants": [
        _participant(0),
        *[_participant(n) for n in range(1, 8)],
        _participant(8, puuid=None, bot=True, riotId=None),
        _participant(9, riotId="sem-tag"),
    ],
}


ROLES = ("TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY")


def _settings(tmp_path: Path, *, key: str | None = "k", reverse: bool = False) -> Settings:
    own = tmp_path / "own.json"
    own.write_text(
        json.dumps(
            {"gameName": "Own", "tagLine": "BR1", "platform": "BR1", "regionalRouting": "AMERICAS"}
        )
    )
    # CC-1214: referência controlada; por padrão, a ordem da Riot já é topo → suporte.
    roles = ROLES[::-1] if reverse else ROLES
    reference = tmp_path / ("reference-reverse.json" if reverse else "reference.json")
    reference.write_text(
        json.dumps({"participations": {f"{51 + n}:{roles[n % 5]}": 1000 for n in range(10)}})
    )
    return Settings(
        ranked_account_file=str(own),
        subject_hash_secret=SECRET,
        riot_api_key=key,
        match_review_reference_path=str(reference),
    )


def _transport(*, account: int = 200, spectator: int = 200, league_429_after: int | None = None):
    seen: list[str] = []

    def responder(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        seen.append(path)
        if "/riot/account/" in path:
            if account != 200:
                return httpx.Response(account)
            return httpx.Response(200, json={"puuid": ME, "gameName": "Live One", "tagLine": "BR1"})
        if "/spectator/v5/" in path:
            assert request.url.host == "br1.api.riotgames.com"
            return httpx.Response(spectator, json=GAME if spectator == 200 else {})
        if "/league/v4/" in path:
            leagues = sum("/league/v4/" in p for p in seen)
            if league_429_after is not None and leagues > league_429_after:
                return httpx.Response(429, headers={"Retry-After": "1"})
            puuid = path.rsplit("/", 1)[-1]
            if puuid.endswith("01"):
                return httpx.Response(200, json=[])  # sem classificação
            return httpx.Response(
                200,
                json=[
                    {
                        "queueType": "RANKED_FLEX_SR",
                        "tier": "GOLD",
                        "rank": "II",
                        "leaguePoints": 41,
                        "wins": 10,
                        "losses": 8,
                        "puuid": puuid,
                    },
                    {
                        "queueType": "RANKED_SOLO_5x5",
                        "tier": "IRON",
                        "rank": "I",
                        "leaguePoints": 3,
                        "wins": 1,
                        "losses": 9,
                        "puuid": puuid,
                    },
                ],
            )
        raise AssertionError(path)

    client = httpx.AsyncClient(transport=httpx.MockTransport(responder))
    budget = RiotFetchBudget(0, LIVE_MAX_REQUESTS, max_total_seconds=30, stop_on_429=True)
    return RiotHttpTransport("synthetic-key", budget, client=client), seen


@pytest.fixture(autouse=True)
def _empty_cache():
    live._CACHE.clear()  # noqa: SLF001 — cache por processo
    live._STATUS_CACHE.clear()  # noqa: SLF001
    yield
    live._CACHE.clear()  # noqa: SLF001
    live._STATUS_CACHE.clear()  # noqa: SLF001


async def _counts(session_factory) -> tuple[int, int]:
    async with session_factory() as session:
        profiles = await session.scalar(select(func.count()).select_from(Profile))
        observations = await session.scalar(select(func.count()).select_from(Observation))
    return int(profiles or 0), int(observations or 0)


@pytest.mark.asyncio
async def test_live_game_teams_ranks_bans_and_nothing_saved(session_factory, tmp_path):
    before = await _counts(session_factory)
    transport, seen = _transport()
    view = await live_game(
        session_factory, _settings(tmp_path), " live one ", "#br1", transport=transport, now=NOW
    )
    assert (view["state"], view["gameName"], view["tagLine"]) == ("IN_GAME", "Live One", "BR1")
    game = view["game"]
    assert game["queueId"] == 440 and game["rankQueue"] == "RANKED_FLEX_SR"
    assert game["lengthSeconds"] == 600 and game["startedAt"] == NOW - timedelta(minutes=10)
    blue, red = game["teams"]
    assert (blue["teamId"], red["teamId"]) == (100, 200)
    bans = [[b["championId"] for b in team["bans"]] for team in (blue, red)]
    assert bans == [[98, 99], [97]]  # ordem de escolha; -1 (sem banimento) fica fora
    assert [p["championId"] for p in blue["participants"]] == [51, 52, 53, 54, 55]
    me, unranked = blue["participants"][0], blue["participants"][1]
    assert me["requested"] and (me["gameName"], me["tagLine"]) == ("Synthetic 0", "BR0")
    assert me["rank"] == {
        "tier": "GOLD",
        "division": "II",
        "leaguePoints": 41,
        "wins": 10,
        "losses": 8,
    }  # elo da fila da partida (Flex), não o Solo/Duo
    assert (me["spell1Id"], me["keystoneId"], me["perkSubStyle"]) == (4, 8005, 8400)
    assert unranked["rankState"] == "UNRANKED" and unranked["rank"] is None
    bot = red["participants"][3]
    assert bot["bot"] and bot["gameName"] is None and bot["rankState"] == "UNAVAILABLE"
    assert red["participants"][4]["gameName"] is None  # Riot ID sem "#"
    assert "synthetic-live-puuid" not in json.dumps(view, default=str)
    # Conta + partida + 9 elos (o bot não tem PUUID); nada gravado no banco.
    assert len(seen) == 11
    assert await _counts(session_factory) == before

    again, seen_again = _transport()
    cached = await live_game(
        session_factory,
        _settings(tmp_path),
        "Live One",
        "BR1",
        transport=again,
        now=NOW + timedelta(seconds=10),
    )
    assert cached is view and seen_again == []
    later, seen_later = _transport()
    await live_game(
        session_factory,
        _settings(tmp_path),
        "Live One",
        "BR1",
        transport=later,
        now=NOW + timedelta(seconds=25),
    )
    assert len(seen_later) == 11


@pytest.mark.asyncio
async def test_not_in_game_and_rank_limit(session_factory, tmp_path):
    transport, seen = _transport(spectator=404)
    view = await live_game(
        session_factory, _settings(tmp_path), "Live One", "BR1", transport=transport
    )
    assert view["state"] == "NOT_IN_GAME" and view["game"] is None and len(seen) == 2

    live._CACHE.clear()  # noqa: SLF001
    limited, _ = _transport(league_429_after=3)
    view = await live_game(
        session_factory, _settings(tmp_path), "Live One", "BR1", transport=limited
    )
    states = [p["rankState"] for t in view["game"]["teams"] for p in t["participants"]]
    assert states.count("UNAVAILABLE") == 7  # 3 elos lidos; o 429 encerra os demais


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kwargs", "code"),
    [({"account": 404}, "PLAYER_NOT_FOUND"), ({"account": 403}, "RIOT_AUTH_FAILED")],
)
async def test_live_errors_are_typed(session_factory, tmp_path, kwargs, code):
    transport, _ = _transport(**kwargs)
    with pytest.raises(PlayerLookupError) as caught:
        await live_game(
            session_factory, _settings(tmp_path), "Live One", "BR1", transport=transport
        )
    assert caught.value.code == code
    with pytest.raises(PlayerLookupError) as missing:
        await live_game(session_factory, _settings(tmp_path, key=None), "Outro", "BR1")
    assert missing.value.code == "RIOT_KEY_MISSING"


@pytest.mark.asyncio
async def test_api_live_game(app_client, tmp_path, monkeypatch):
    app = app_client._transport.app  # noqa: SLF001 — o teste troca a configuração da app
    app.state.settings = _settings(tmp_path)
    transport, _ = _transport()
    monkeypatch.setattr(live, "_transport", lambda _settings: transport)
    response = await app_client.post(
        "/api/v1/players/live", json={"gameName": "Live One", "tagLine": "BR1"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "IN_GAME" and len(body["game"]["teams"][0]["participants"]) == 5
    assert "puuid" not in response.text.lower()
    invalid = await app_client.post("/api/v1/players/live", json={"gameName": "x", "tagLine": "B"})
    assert invalid.status_code == 422 and invalid.json()["code"] == "INVALID_RIOT_ID"


@pytest.mark.asyncio
async def test_live_status_uses_two_requests_and_caches(session_factory, tmp_path):
    """CC-1213: aviso "jogando agora" só com conta e partida; 60 s em memória."""
    transport, seen = _transport()
    status = await live_status(_settings(tmp_path), "Live One", "BR1", transport=transport, now=NOW)
    assert status == {
        "state": "IN_GAME",
        "gameName": "Live One",
        "tagLine": "BR1",
        "checkedAt": NOW,
        "queueId": 440,
        "gameMode": "CLASSIC",
        "startedAt": NOW - timedelta(minutes=10),
    }
    assert len(seen) == 2 and not any("/league/" in path for path in seen)

    again, seen_again = _transport()
    cached = await live_status(
        _settings(tmp_path), "Live One", "BR1", transport=again, now=NOW + timedelta(seconds=50)
    )
    assert cached is status and seen_again == []

    live._STATUS_CACHE.clear()  # noqa: SLF001
    out, seen_out = _transport(spectator=404)
    later = await live_status(
        _settings(tmp_path), "Live One", "BR1", transport=out, now=NOW + timedelta(minutes=2)
    )
    assert later["state"] == "NOT_IN_GAME" and later["startedAt"] is None and len(seen_out) == 2


@pytest.mark.asyncio
async def test_live_status_reuses_the_full_live_game(session_factory, tmp_path):
    full, _ = _transport()
    await live_game(
        session_factory, _settings(tmp_path), "Live One", "BR1", transport=full, now=NOW
    )
    unused, seen = _transport()
    status = await live_status(
        _settings(tmp_path), "Live One", "BR1", transport=unused, now=NOW + timedelta(seconds=5)
    )
    assert status["state"] == "IN_GAME" and status["queueId"] == 440 and seen == []
    with pytest.raises(PlayerLookupError) as caught:
        await live_status(_settings(tmp_path, key=None), "Outro", "BR1")
    assert caught.value.code == "RIOT_KEY_MISSING"
    missing, _ = _transport(account=404)
    with pytest.raises(PlayerLookupError) as not_found:
        await live_status(_settings(tmp_path), "Ninguém", "BR1", transport=missing)
    assert not_found.value.code == "PLAYER_NOT_FOUND"


@pytest.mark.asyncio
async def test_api_live_status(app_client, tmp_path, monkeypatch):
    app = app_client._transport.app  # noqa: SLF001 — o teste troca a configuração da app
    app.state.settings = _settings(tmp_path)
    transport, _ = _transport()
    monkeypatch.setattr(live, "_transport", lambda _settings, _max=2: transport)
    response = await app_client.post(
        "/api/v1/players/live/status", json={"gameName": "Live One", "tagLine": "BR1"}
    )
    assert response.status_code == 200
    assert response.json()["state"] == "IN_GAME" and "puuid" not in response.text.lower()


@pytest.mark.asyncio
async def test_live_teams_in_lane_order_with_estimated_positions(
    session_factory, tmp_path, monkeypatch
):
    """CC-1214: a Riot não informa a rota ao vivo; no Summoner's Rift ela é estimada e os times
    saem na ordem topo, selva, meio, atirador e suporte."""
    transport, _ = _transport()
    view = await live_game(
        session_factory, _settings(tmp_path, reverse=True), "Live One", "BR1", transport=transport
    )
    game = view["game"]
    assert game["positionsEstimated"] is True
    blue = game["teams"][0]["participants"]
    assert [p["championId"] for p in blue] == [55, 54, 53, 52, 51]
    assert [p["position"] for p in blue] == list(ROLES)

    live._CACHE.clear()  # noqa: SLF001
    monkeypatch.setitem(GAME, "mapId", 12)  # ARAM: sem rotas, na ordem da Riot
    aram, _ = _transport()
    view = await live_game(session_factory, _settings(tmp_path), "Live One", "BR1", transport=aram)
    assert view["game"]["positionsEstimated"] is False
    blue = view["game"]["teams"][0]["participants"]
    assert [p["championId"] for p in blue] == [51, 52, 53, 54, 55]
    assert all(p["position"] is None for p in blue)
