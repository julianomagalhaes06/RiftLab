"""CC-1101: perfil de jogador por Riot ID (transporte Riot simulado, sem rede)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from sqlalchemy import delete, select

from riftlab.config import Settings
from riftlab.history_sync.application import process_next_history_sync, request_sync
from riftlab.history_sync.models import HistorySyncRunModel as Run
from riftlab.ingestion.infrastructure.riot_transport import RiotFetchBudget, RiotHttpTransport
from riftlab.ingestion.infrastructure.subject_hash import compute_real_subject_hash
from riftlab.ingestion.models import ImportJobModel, MatchModel, ParticipantModel
from riftlab.players import application
from riftlab.players.application import lookup_player, profile_account
from riftlab.players.models import PlayerProfileModel as Profile
from riftlab.ranked.models import RankedObservationModel as Observation
from riftlab.shared.db import unit_of_work
from riftlab.shared.errors import PlayerLookupError

PUUID = "synthetic-player-puuid"
SECRET = "synthetic-secret"
NOW = datetime(2026, 10, 19, 15, tzinfo=UTC)
NEW_MATCH = "BR1_9200000001"


def _settings(tmp_path: Path, *, key: str | None = "k") -> Settings:
    own = tmp_path / "own.json"
    own.write_text(
        json.dumps(
            {
                "gameName": "Synthetic Own",
                "tagLine": "BR1",
                "platform": "BR1",
                "regionalRouting": "AMERICAS",
            }
        )
    )
    return Settings(ranked_account_file=str(own), subject_hash_secret=SECRET, riot_api_key=key)


def _transport(*, account: int = 200, summoner: int = 200, league: int = 200, ids=()):
    seen: list[str] = []

    def responder(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        seen.append(path)
        if "/riot/account/" in path:
            if account != 200:
                return httpx.Response(account)
            return httpx.Response(
                200, json={"puuid": PUUID, "gameName": "Synthetic Third", "tagLine": "XYZ"}
            )
        if "/summoner/v4/" in path:
            assert request.url.host == "br1.api.riotgames.com"
            if summoner != 200:
                return httpx.Response(summoner)
            return httpx.Response(200, json={"profileIconId": 4567, "summonerLevel": 321})
        if "/league/v4/" in path:
            if league != 200:
                return httpx.Response(league, headers={"Retry-After": "7"})
            return httpx.Response(
                200,
                json=[
                    {
                        "queueType": "RANKED_SOLO_5x5",
                        "tier": "GOLD",
                        "rank": "II",
                        "leaguePoints": 41,
                        "wins": 10,
                        "losses": 8,
                        "puuid": PUUID,
                    }
                ],
            )
        if path.endswith(f"{PUUID}/ids"):
            return httpx.Response(200, json=list(ids))
        raise AssertionError(path)

    client = httpx.AsyncClient(transport=httpx.MockTransport(responder))
    budget = RiotFetchBudget(0, 3, max_total_seconds=30, stop_on_429=True)
    return RiotHttpTransport("synthetic-key", budget, client=client), seen


@pytest.fixture
async def clean(session_factory):
    async def wipe() -> None:
        async with unit_of_work(session_factory) as session:
            keys = (await session.scalars(select(Profile.account_key))).all()
            await session.execute(delete(Run))
            await session.execute(delete(Observation).where(Observation.account_key.in_(keys)))
            await session.execute(delete(Profile))
            await session.execute(
                delete(ImportJobModel).where(ImportJobModel.requested_match_id == NEW_MATCH)
            )

    await wipe()
    yield
    await wipe()


@pytest.mark.asyncio
async def test_lookup_saves_profile_rank_and_never_the_puuid(session_factory, tmp_path, clean):
    settings = _settings(tmp_path)
    transport, seen = _transport()
    view = await lookup_player(
        session_factory, settings, "  synthetic third ", "#xyz", transport=transport, now=NOW
    )
    assert (view["gameName"], view["tagLine"]) == ("Synthetic Third", "XYZ")  # canônico
    assert (view["profileIconId"], view["summonerLevel"]) == (4567, 321)
    assert view["solo"]["tier"] == "GOLD" and view["solo"]["leaguePoints"] == 41
    assert view["flex"]["state"] == "UNRANKED"
    assert PUUID not in json.dumps(view, default=str)
    assert len(seen) == 3
    async with session_factory() as session:
        profile = await session.scalar(select(Profile))
        assert profile is not None
        assert profile.subject_hash == compute_real_subject_hash(PUUID, SECRET)
        assert PUUID not in json.dumps(
            {c.name: str(getattr(profile, c.name)) for c in Profile.__table__.columns}
        )

    # Recente: sem rede. Pedido explícito: consulta de novo.
    cached, seen_again = _transport()
    again = await lookup_player(
        session_factory,
        settings,
        "SYNTHETIC THIRD",
        "xyz",
        transport=cached,
        now=NOW + timedelta(minutes=5),
    )
    assert again["id"] == view["id"] and seen_again == []
    await lookup_player(
        session_factory,
        settings,
        "Synthetic Third",
        "XYZ",
        transport=cached,
        now=NOW + timedelta(minutes=6),
        refresh=True,
    )
    assert len(seen_again) == 3

    # Sem chave: devolve o guardado com o motivo, sem rede.
    offline = await lookup_player(
        session_factory, _settings(tmp_path, key=None), "Synthetic Third", "XYZ", now=NOW
    )
    assert offline["staleReason"] == "RIOT_KEY_MISSING"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kwargs", "code"),
    [
        ({"account": 404}, "PLAYER_NOT_FOUND"),
        ({"summoner": 404}, "PLAYER_NOT_ON_PLATFORM"),
        ({"account": 403}, "RIOT_AUTH_FAILED"),
        ({"league": 429}, "RIOT_RATE_LIMITED"),
    ],
)
async def test_lookup_errors_are_typed_and_save_nothing(
    session_factory, tmp_path, clean, kwargs, code
):
    transport, _ = _transport(**kwargs)
    with pytest.raises(PlayerLookupError) as caught:
        await lookup_player(
            session_factory,
            _settings(tmp_path),
            "Synthetic Third",
            "XYZ",
            transport=transport,
            now=NOW,
        )
    assert caught.value.code == code
    async with session_factory() as session:
        assert await session.scalar(select(Profile)) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("name", "tag"), [("", "BR1"), ("a#b", "BR1"), ("Nome", "B"), ("Nome", "BR-1")]
)
async def test_invalid_riot_id_never_reaches_the_riot(session_factory, tmp_path, clean, name, tag):
    transport, seen = _transport()
    with pytest.raises(PlayerLookupError) as caught:
        await lookup_player(session_factory, _settings(tmp_path), name, tag, transport=transport)
    assert caught.value.code == "INVALID_RIOT_ID" and seen == []


@pytest.mark.asyncio
async def test_api_lookup_profile_sync_and_player_match_list(
    app_client, session_factory, tmp_path, clean, monkeypatch
):
    settings = _settings(tmp_path)
    app = app_client._transport.app  # noqa: SLF001 — o teste troca a configuração da app
    app.state.settings = settings
    transport, _ = _transport(ids=[NEW_MATCH])
    monkeypatch.setattr(application, "_transport", lambda _settings: transport)

    response = await app_client.post(
        "/api/v1/players/lookup", json={"gameName": "Synthetic Third", "tagLine": "XYZ"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["profileIconId"] == 4567 and "puuid" not in json.dumps(body).lower()
    player_id = body["id"]
    assert (await app_client.get(f"/api/v1/players/{player_id}")).json()["gameName"] == (
        "Synthetic Third"
    )

    # Sincronização do histórico do jogador: o worker usa o Riot ID guardado do perfil.
    created = await app_client.post(f"/api/v1/players/{player_id}/sync")
    assert created.status_code == 202 and created.json()["state"] == "QUEUED"
    worker_transport, _ = _transport(ids=[NEW_MATCH])
    assert await process_next_history_sync(session_factory, settings, transport=worker_transport)
    status = (await app_client.get(f"/api/v1/players/{player_id}/sync")).json()
    assert (status["state"], status["listed"], status["newEnqueued"]) == ("SUCCEEDED", 1, 1)
    async with session_factory() as session:
        assert await session.scalar(
            select(ImportJobModel).where(ImportJobModel.requested_match_id == NEW_MATCH)
        )
    # A conta configurada continua com a própria sincronização (outra chave).
    own, own_created = await request_sync(session_factory, settings, now=NOW)
    assert own_created and own["state"] == "QUEUED"
    assert (await profile_account(session_factory, body_id(player_id))).game_name == (
        "Synthetic Third"
    )

    # Lista filtrada pelo jogador: nenhuma partida dele importada ainda; id desconhecido é 404.
    listing = await app_client.get("/api/v1/matches", params={"player": player_id})
    assert listing.status_code == 200 and listing.json()["items"] == []
    missing = await app_client.get(
        "/api/v1/matches", params={"player": "00000000-0000-4000-8000-000000000000"}
    )
    assert missing.status_code == 404

    not_found_transport, _ = _transport(account=404)
    monkeypatch.setattr(application, "_transport", lambda _settings: not_found_transport)
    problem = await app_client.post(
        "/api/v1/players/lookup", json={"gameName": "Ninguem Aqui", "tagLine": "000"}
    )
    assert problem.status_code == 404 and problem.json()["code"] == "PLAYER_NOT_FOUND"
    assert "Ninguem" not in problem.text
    invalid = await app_client.post(
        "/api/v1/players/lookup", json={"gameName": "Nome", "tagLine": "B"}
    )
    assert invalid.status_code == 422 and invalid.json()["code"] == "INVALID_RIOT_ID"


def body_id(value: str):  # noqa: ANN201
    import uuid

    return uuid.UUID(value)


@pytest.mark.asyncio
async def test_player_match_list_marks_the_player_as_local(app_client, session_factory, clean):
    """Só as partidas reais em que o jogador aparece, com o participante dele como local."""
    subject = compute_real_subject_hash(PUUID, SECRET)
    other = "f" * 64
    mine, theirs = "BR1_9200000011", "BR1_9200000012"
    try:
        async with unit_of_work(session_factory) as session:
            for n, match_id in enumerate((mine, theirs)):
                session.add(
                    MatchModel(
                        match_id=match_id,
                        queue=420,
                        map_id=11,
                        patch="16.20",
                        start_at=NOW - timedelta(minutes=n),
                        duration_seconds=1800,
                        raw_hash="synthetic",
                    )
                )
            await session.flush()
            session.add(
                ParticipantModel(
                    match_id=mine, participant_id=4, champion_id=51, team=100, subject_hash=subject
                )
            )
            session.add(
                ParticipantModel(
                    match_id=theirs, participant_id=4, champion_id=51, team=100, subject_hash=other
                )
            )
            session.add(
                Profile(
                    account_key="k" * 64,
                    game_name="Synthetic Third",
                    tag_line="XYZ",
                    subject_hash=subject,
                    refreshed_at=NOW,
                )
            )
        async with session_factory() as session:
            player_id = await session.scalar(select(Profile.id))
        items = (
            await app_client.get("/api/v1/matches", params={"player": str(player_id), "limit": 100})
        ).json()["items"]
        assert [item["matchId"] for item in items] == [mine]
        assert items[0]["localParticipantId"] == 4
    finally:
        async with unit_of_work(session_factory) as session:
            await session.execute(
                delete(ParticipantModel).where(ParticipantModel.match_id.in_([mine, theirs]))
            )
            await session.execute(delete(MatchModel).where(MatchModel.match_id.in_([mine, theirs])))
