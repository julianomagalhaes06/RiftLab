"""CC-1101: perfil de jogador por Riot ID (transporte Riot simulado, sem rede)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from sqlalchemy import delete, inspect, select, text
from sqlalchemy.exc import IntegrityError

from riftlab.catalog.models import CatalogVersionModel
from riftlab.config import Settings
from riftlab.history_sync.application import (
    configured_account_key,
    process_next_history_sync,
    request_sync,
)
from riftlab.history_sync.models import HistorySyncRunModel as Run
from riftlab.ingestion.infrastructure.raw_store import DurableRawMatchStore
from riftlab.ingestion.infrastructure.riot_transport import RiotFetchBudget, RiotHttpTransport
from riftlab.ingestion.infrastructure.subject_hash import compute_real_subject_hash
from riftlab.ingestion.models import ImportJobModel, MatchModel, ParticipantModel
from riftlab.ingestion.purge import retention
from riftlab.players import application
from riftlab.players.application import (
    lookup_player,
    own_player,
    profile_account,
    purge_player,
)
from riftlab.players.models import PlayerProfileModel as Profile
from riftlab.ranked.domain import account_key
from riftlab.ranked.models import RankedObservationModel as Observation
from riftlab.shared.db import unit_of_work
from riftlab.shared.errors import NotFoundError, PlayerLookupError

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


def _transport(
    *,
    account: int = 200,
    summoner: int = 200,
    league: int = 200,
    ids=(),
    riot_id=("Synthetic Third", "XYZ"),
):
    seen: list[str] = []

    def responder(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        seen.append(path)
        if "/riot/account/" in path:
            if account != 200:
                return httpx.Response(account)
            return httpx.Response(
                200, json={"puuid": PUUID, "gameName": riot_id[0], "tagLine": riot_id[1]}
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
        stored = json.dumps(
            {c.name: str(getattr(profile, c.name)) for c in Profile.__table__.columns}
        )
        # Nem o PUUID nem o Riot ID ficam no banco (CC-1103).
        assert PUUID not in stored and "Synthetic" not in stored and "XYZ" not in stored

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
    # Sem consulta, o Riot ID da resposta é o do pedido (o banco não o guarda).
    assert (again["gameName"], again["tagLine"]) == ("SYNTHETIC THIRD", "xyz")
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
    # O perfil não é mais lido por id (o banco não tem o Riot ID para devolver).
    assert (await app_client.get(f"/api/v1/players/{player_id}")).status_code in (404, 405)

    # Sincronização do histórico do jogador: o pedido traz o Riot ID, conferido pelo HMAC.
    riot_id = {"gameName": "Synthetic Third", "tagLine": "XYZ"}
    other = await app_client.post(
        f"/api/v1/players/{player_id}/sync", json={"gameName": "Outro", "tagLine": "XYZ"}
    )
    assert other.status_code == 404
    assert (await app_client.post(f"/api/v1/players/{player_id}/sync")).status_code == 422
    created = await app_client.post(f"/api/v1/players/{player_id}/sync", json=riot_id)
    assert created.status_code == 202 and created.json()["state"] == "QUEUED"
    async with session_factory() as session:
        queued = await session.scalar(select(Run).where(Run.state == "QUEUED"))
        assert queued is not None
        assert (queued.game_name, queued.tag_line) == ("Synthetic Third", "XYZ")
    worker_transport, _ = _transport(ids=[NEW_MATCH])
    assert await process_next_history_sync(session_factory, settings, transport=worker_transport)
    status = (await app_client.get(f"/api/v1/players/{player_id}/sync")).json()
    assert (status["state"], status["listed"], status["newEnqueued"]) == ("SUCCEEDED", 1, 1)
    async with session_factory() as session:
        assert await session.scalar(
            select(ImportJobModel).where(ImportJobModel.requested_match_id == NEW_MATCH)
        )
        # Terminada, a execução não guarda mais o Riot ID.
        finished = (await session.scalars(select(Run))).all()
        assert finished and all(r.game_name is None and r.tag_line is None for r in finished)
    # A conta configurada continua com a própria sincronização (outra chave).
    own, own_created = await request_sync(session_factory, settings, now=NOW)
    assert own_created and own["state"] == "QUEUED"
    account = await profile_account(
        session_factory, settings, body_id(player_id), "synthetic third", "xyz"
    )
    assert account.game_name == "synthetic third"

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


@pytest.mark.asyncio
async def test_no_table_keeps_a_riot_id_at_rest(session_factory, clean):
    """Guarda de esquema (CC-1103): só a execução ativa da sincronização tem Riot ID, e o banco
    recusa uma execução terminada que ainda o tenha."""
    async with session_factory() as session:
        conn = await session.connection()
        columns = await conn.run_sync(
            lambda sync: {
                table: {c["name"] for c in inspect(sync).get_columns(table)}
                for table in inspect(sync).get_table_names()
            }
        )
    holders = {t for t, cols in columns.items() if cols & {"game_name", "tag_line"}}
    assert holders == {"history_sync_runs"}
    with pytest.raises(IntegrityError):
        async with unit_of_work(session_factory) as session:
            session.add(
                Run(
                    account_key="r" * 64,
                    state="SUCCEEDED",
                    requested_at=NOW,
                    finished_at=NOW,
                    game_name="Synthetic Third",
                    tag_line="XYZ",
                )
            )


@pytest.mark.asyncio
async def test_failed_player_sync_also_drops_the_riot_id(session_factory, tmp_path, clean):
    settings = _settings(tmp_path)
    third = application.RiotAccountConfig(
        "Synthetic Third", "XYZ", application.ALLOWED_PLATFORM, application.ALLOWED_REGIONAL_ROUTING
    )
    _, created = await request_sync(session_factory, settings, now=NOW, account=third)
    assert created
    failing, _ = _transport(account=503)
    assert await process_next_history_sync(session_factory, settings, transport=failing, now=NOW)
    async with session_factory() as session:
        run = await session.scalar(select(Run))
        assert run is not None and run.state == "FAILED"
        assert (run.game_name, run.tag_line) == (None, None)


async def _seed_profile(factory, settings, name: str, tag: str, refreshed: datetime) -> str:
    key = account_key(
        application.RiotAccountConfig(
            name, tag, application.ALLOWED_PLATFORM, application.ALLOWED_REGIONAL_ROUTING
        ),
        SECRET,
    )
    assert key is not None
    async with unit_of_work(factory) as session:
        session.add(Profile(account_key=key, subject_hash="s" * 64, refreshed_at=refreshed))
        for days in (0, 1):  # duas observações: nenhuma é preservada para terceiros
            session.add(
                Observation(
                    account_key=key,
                    queue="RANKED_SOLO_5x5",
                    season="2026",
                    captured_at=refreshed - timedelta(days=days),
                    state="UNRANKED",
                    origin="RIOT_LEAGUE_V4",
                )
            )
        session.add(
            Run(account_key=key, state="SUCCEEDED", requested_at=refreshed, finished_at=refreshed)
        )
    return key


async def _rows(factory, key: str) -> tuple[int, int, int]:
    counts = []
    async with factory() as session:
        for model in (Profile, Observation, Run):
            rows = await session.scalars(select(model).where(model.account_key == key))
            counts.append(len(rows.all()))
    return counts[0], counts[1], counts[2]


@pytest.mark.asyncio
async def test_retention_drops_old_third_party_profiles_and_keeps_the_own_account(
    session_factory, tmp_path, clean
):
    settings = _settings(tmp_path)
    own_key = configured_account_key(settings)
    assert own_key is not None
    old, recent = NOW - timedelta(days=30), NOW - timedelta(days=1)
    stale = await _seed_profile(session_factory, settings, "Velho", "AAA", old)
    fresh = await _seed_profile(session_factory, settings, "Novo", "BBB", recent)
    own = await _seed_profile(session_factory, settings, "Synthetic Own", "BR1", old)
    assert own == own_key
    raw = DurableRawMatchStore(tmp_path / "raw")
    common = {"match_days": 100000, "rank_days": 100000, "secret": SECRET, "now": NOW}
    common |= {"profile_days": 7, "own_key": own_key}
    try:
        plan = await retention(session_factory, raw, **common)
        assert (plan["playerProfiles"], plan["playerRankObservations"]) == (1, 2)
        assert await _rows(session_factory, stale) == (1, 2, 1)  # só listou

        done = await retention(session_factory, raw, execute=True, **common)
        assert done["deletedPlayerProfiles"] == 1
        assert await _rows(session_factory, stale) == (0, 0, 0)
        assert await _rows(session_factory, fresh) == (1, 2, 1)
        assert await _rows(session_factory, own) == (1, 2, 1)
        assert "Velho" not in json.dumps(done) and stale not in json.dumps(done)
    finally:
        async with unit_of_work(session_factory) as session:
            await session.execute(delete(Observation).where(Observation.account_key == own))


@pytest.mark.asyncio
async def test_retention_drops_the_riot_id_of_an_active_run_of_an_expired_profile(
    session_factory, tmp_path, clean
):
    # Execução que o worker abandonou (CC-1216): o perfil é o único caminho até ela.
    settings = _settings(tmp_path)
    stale = await _seed_profile(session_factory, settings, "Velho", "AAA", NOW - timedelta(days=30))
    async with unit_of_work(session_factory) as session:
        await session.execute(
            text(
                "INSERT INTO history_sync_runs (id, account_key, state, game_name, tag_line, "
                "requested_at, started_at) VALUES (gen_random_uuid(), :key, 'RUNNING', 'Velho', "
                "'AAA', :at, :at)"
            ),
            {"key": stale, "at": NOW - timedelta(days=29)},
        )
    raw = DurableRawMatchStore(tmp_path / "raw")
    common = {"match_days": 100000, "rank_days": 100000, "secret": SECRET, "now": NOW}
    done = await retention(
        session_factory, raw, execute=True, profile_days=7, own_key=None, **common
    )
    assert done["deletedPlayerProfiles"] == 1
    async with session_factory() as session:
        run = await session.scalar(
            select(Run).where(Run.account_key == stale, Run.state == "RUNNING")
        )
        assert run is not None and (run.game_name, run.tag_line) == (None, None)


@pytest.mark.asyncio
async def test_purge_player_lists_then_deletes_one_riot_id(session_factory, tmp_path, clean):
    settings = _settings(tmp_path)
    target = await _seed_profile(session_factory, settings, "Alvo", "CCC", NOW)
    other = await _seed_profile(session_factory, settings, "Outro", "DDD", NOW)
    async with unit_of_work(session_factory) as session:
        await session.execute(
            text(
                "INSERT INTO history_sync_runs (id, account_key, state, game_name, tag_line) "
                "VALUES (gen_random_uuid(), :key, 'QUEUED', 'Alvo', 'CCC')"
            ),
            {"key": target},
        )
    plan = await purge_player(session_factory, settings, " alvo ", "#ccc")
    assert plan == {
        "dryRun": True,
        "playerProfiles": 1,
        "rankObservations": 2,
        "syncRuns": 1,
        "activeSyncRuns": 1,
    }
    assert await _rows(session_factory, target) == (1, 2, 2)
    await purge_player(session_factory, settings, "Alvo", "CCC", execute=True)
    # A execução ativa fica, mas sem o Riot ID desde já (CC-1216): não depende do worker.
    assert await _rows(session_factory, target) == (0, 0, 1)
    async with session_factory() as session:
        active = await session.scalar(select(Run).where(Run.account_key == target))
        assert active is not None and active.state == "QUEUED"
        assert (active.game_name, active.tag_line) == (None, None)
    assert await _rows(session_factory, other) == (1, 2, 1)
    with pytest.raises(PlayerLookupError):
        await purge_player(session_factory, settings, "Alvo", "C")


@pytest.mark.asyncio
async def test_own_player_looks_up_once_then_serves_the_stored_profile(
    session_factory, tmp_path, clean
):
    """CC-1202: chip do topo. Sem perfil guardado, uma consulta; depois só o banco. O Riot ID
    vem do arquivo da conta, nunca do banco (CC-1103)."""
    with pytest.raises(PlayerLookupError) as caught:
        await own_player(session_factory, _settings(tmp_path, key=None), now=NOW)
    assert caught.value.code == "RIOT_KEY_MISSING"

    settings = _settings(tmp_path)
    first, seen = _transport(riot_id=("Synthetic Own", "BR1"))
    view = await own_player(session_factory, settings, transport=first, now=NOW)
    assert (view["gameName"], view["profileIconId"], len(seen)) == ("Synthetic Own", 4567, 3)

    later, seen_later = _transport(riot_id=("Synthetic Own", "BR1"))
    again = await own_player(
        session_factory, settings, transport=later, now=NOW + timedelta(days=3)
    )
    assert again["id"] == view["id"] and seen_later == []
    assert again["gameName"] == "Synthetic Own"
    offline = await own_player(session_factory, _settings(tmp_path, key=None), now=NOW)
    assert offline["id"] == view["id"]

    missing = Settings(ranked_account_file=str(tmp_path / "nada.json"), subject_hash_secret=SECRET)
    with pytest.raises(NotFoundError):
        await own_player(session_factory, missing, now=NOW)


@pytest.mark.asyncio
async def test_api_own_player_and_current_catalog(app_client, session_factory, tmp_path, clean):
    app = app_client._transport.app  # noqa: SLF001 — o teste troca a configuração da app
    app.state.settings = _settings(tmp_path, key=None)
    # Sem perfil: 200 com o motivo (o navegador não registra erro no console a cada tela).
    unavailable = await app_client.get("/api/v1/players/me")
    assert unavailable.status_code == 200
    assert unavailable.json() == {"profile": None, "reason": "RIOT_KEY_MISSING"}
    app.state.settings = Settings(
        ranked_account_file=str(tmp_path / "nada.json"), subject_hash_secret=SECRET
    )
    missing = (await app_client.get("/api/v1/players/me")).json()
    assert missing == {"profile": None, "reason": "ACCOUNT_NOT_CONFIGURED"}
    app.state.settings = _settings(tmp_path, key=None)

    first, _ = _transport(riot_id=("Synthetic Own", "BR1"))
    await own_player(session_factory, _settings(tmp_path), transport=first, now=NOW)
    body = (await app_client.get("/api/v1/players/me")).json()
    assert body["reason"] is None and body["profile"]["gameName"] == "Synthetic Own"
    assert "puuid" not in json.dumps(body).lower()

    # Patches fora do formato do jogo (sintéticos de outros testes) não contam.
    rows = [("999.99", "999.99.1"), ("1234.5", "1234.5.1"), ("DEMO", "0.0.1")]
    try:
        async with unit_of_work(session_factory) as session:
            for patch, version in rows:
                session.add(
                    CatalogVersionModel(
                        game_patch=patch,
                        data_dragon_version=version,
                        sha256=f"synthetic-cc1202-{patch}",
                        mapping_version="synthetic",
                        raw_hash="synthetic",
                        curation_rules_version="synthetic",
                    )
                )
        current = (await app_client.get("/api/v1/catalog/current")).json()
        assert current == {"gamePatch": "999.99", "dataDragonVersion": "999.99.1"}
    finally:
        async with unit_of_work(session_factory) as session:
            await session.execute(
                delete(CatalogVersionModel).where(
                    CatalogVersionModel.sha256.in_([f"synthetic-cc1202-{p}" for p, _ in rows])
                )
            )
