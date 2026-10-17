"""GET /api/v1/matches, GET /api/v1/matches/{matchId} e
POST /api/v1/matches/{matchId}/preparation (CC-205) contra PostgreSQL real e a
app real. As leituras só consultam o banco; nenhum identificador pessoal sai;
a ordem da lista é estável (startAt desc, matchId desc) e a paginação por
cursor não repete nem pula partidas."""

from __future__ import annotations

import base64
import dataclasses
import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from pipeline_helpers import (
    DEMO_1_MAPPING,
    FAKE_SUBJECT_HASH_SECRET,
    StubCatalogSource,
    import_demo_match,
    import_real_match,
    purge_open_preparations,
    reset_match,
    stub_catalog_factory,
    sync_demo_catalog,
    unique_patch,
    write_prepare_configs,
)
from sqlalchemy import select

from riftlab.api.app import create_app
from riftlab.catalog.application import resolve_catalog_for_match, sync_catalog
from riftlab.catalog.infrastructure.demo_source import DemoCatalogSource
from riftlab.config import Settings
from riftlab.ingestion.domain import Match
from riftlab.ingestion.infrastructure.raw_store import DurableRawMatchStore
from riftlab.ingestion.models import MatchModel, ParticipantModel
from riftlab.ingestion.repository import SqlAlchemyIngestionRepository
from riftlab.preparation.application import process_next_preparation
from riftlab.shared.db import unit_of_work

_PROBLEM_KEYS = {"type", "title", "status", "code", "detail", "requestId"}
_PERSONAL = ("subject", "hash", "puuid", "riotid", "gamename", "tagline")


@pytest_asyncio.fixture(autouse=True)
async def _isolated_preparation_queue(session_factory) -> AsyncIterator[None]:
    await purge_open_preparations(session_factory)
    yield
    await purge_open_preparations(session_factory)


def _settings() -> Settings:
    return Settings(subject_hash_secret=FAKE_SUBJECT_HASH_SECRET)


def _assert_problem(response, status: int, code: str, echoed: str | None = None) -> None:
    assert response.status_code == status
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    assert set(body) == _PROBLEM_KEYS
    assert body["code"] == code and body["status"] == status
    if echoed:  # nunca ecoa o que o cliente enviou
        assert echoed not in response.text


def _keys(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            found.add(key)
            found |= _keys(item)
    elif isinstance(value, list):
        for item in value:
            found |= _keys(item)
    return found


# ---------------------------------------------------------------------------
# Lista: ordem estável, paginação por cursor, só partidas importadas
# ---------------------------------------------------------------------------

_LATE = datetime(
    2099, 6, 1, 12, 0, 0, 123456, tzinfo=UTC
)  # antes de qualquer partida real do banco


async def _insert_match(session_factory, match_id: str, start_at: datetime) -> None:
    async with unit_of_work(session_factory) as session:
        await SqlAlchemyIngestionRepository(session).add_match(
            Match(
                match_id=match_id,
                queue=420,
                map_id=11,
                patch="16.17",
                start_at=start_at,
                duration_seconds=1800,
                raw_hash="0" * 64,
            )
        )


async def _delete_matches(session_factory, match_ids: list[str]) -> None:
    async with unit_of_work(session_factory) as session:
        for match_id in match_ids:
            row = await session.get(MatchModel, match_id)
            if row is not None:
                await session.delete(row)


async def _walk(client, *, limit: int, until: int | None = None) -> list[dict[str, Any]]:
    """Percorre as páginas; `until` para depois de N itens (partidas próprias)."""
    items: list[dict[str, Any]] = []
    cursor: str | None = None
    for _ in range(500):  # teto de segurança
        params: dict[str, Any] = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        response = await client.get("/api/v1/matches", params=params)
        assert response.status_code == 200, response.text
        page = response.json()
        assert len(page["items"]) <= limit
        if page["nextCursor"] is not None:
            assert page["items"], "página vazia com nextCursor"
            assert page["nextCursor"] != ""
        items.extend(page["items"])
        if until is not None and len(items) >= until:
            return items
        cursor = page["nextCursor"]
        if cursor is None:
            return items
    raise AssertionError("paginação não terminou")


async def test_list_orders_by_start_desc_then_match_id_desc_and_pages_without_gaps(
    app_client, session_factory
) -> None:
    tag = uuid.uuid4().int % 10**6
    ids = {name: f"BR1_9{tag:06d}{name}" for name in "1234567"}  # sufixo numérico: 1..7
    newest = _LATE + timedelta(days=2)
    same_a = _LATE + timedelta(days=1)  # empate de startAt entre 1, 2 e 3
    oldest = _LATE
    layout = {
        ids["1"]: same_a,
        ids["2"]: same_a,
        ids["3"]: same_a,
        ids["4"]: oldest,
        ids["5"]: oldest,
        ids["6"]: _LATE - timedelta(days=1),
    }
    inserted = list(layout)
    try:
        for match_id, start_at in layout.items():
            await _insert_match(session_factory, match_id, start_at)
        expected = sorted(layout, key=lambda m: (layout[m], m), reverse=True)

        for limit in (1, 2, 4, 100):
            walked = await _walk(app_client, limit=limit, until=len(expected))
            assert [item["matchId"] for item in walked[: len(expected)]] == expected, limit
            assert len({item["matchId"] for item in walked}) == len(walked)  # nenhuma repetida

        # uma partida mais nova aparecendo no meio da paginação não desloca as páginas seguintes
        first = (await app_client.get("/api/v1/matches", params={"limit": 2})).json()
        assert [item["matchId"] for item in first["items"]] == expected[:2]
        await _insert_match(session_factory, ids["7"], newest)
        inserted.append(ids["7"])
        second = (
            await app_client.get(
                "/api/v1/matches", params={"limit": 2, "cursor": first["nextCursor"]}
            )
        ).json()
        assert [item["matchId"] for item in second["items"]] == expected[2:4]
        head = (await app_client.get("/api/v1/matches", params={"limit": 1})).json()
        assert head["items"][0]["matchId"] == ids["7"]  # e a lista nova a mostra primeiro
    finally:
        await _delete_matches(session_factory, inserted)


async def test_list_items_have_no_personal_field_and_report_not_requested_without_a_job(
    app_client, session_factory
) -> None:
    match_id = f"BR1_9{uuid.uuid4().int % 10**9:09d}"
    try:
        await _insert_match(session_factory, match_id, _LATE + timedelta(days=30))
        response = await app_client.get("/api/v1/matches", params={"limit": 1})
        [item] = response.json()["items"]
        assert item == {
            "matchId": match_id,
            "queue": 420,
            "map": 11,
            "patch": "16.17",
            "startAt": (_LATE + timedelta(days=30)).isoformat().replace("+00:00", "Z"),
            "durationSeconds": 1800,
            "preparationStatus": "NOT_REQUESTED",
            "championsPresent": [],
            "supportLevel": "LIMITED",
            "supportReason": "PATCH_NOT_VALIDATED",
            "participants": [],
            "localParticipantId": None,
        }
    finally:
        await _delete_matches(session_factory, [match_id])


async def test_list_last_page_has_no_next_cursor_and_default_limit_is_20(
    app_client, session_factory
) -> None:
    tag = uuid.uuid4().int % 10**6
    ids = [f"BR1_8{tag:06d}{n}" for n in range(3)]
    try:
        for offset, match_id in enumerate(ids):
            await _insert_match(session_factory, match_id, _LATE + timedelta(days=100 + offset))
        default = (await app_client.get("/api/v1/matches")).json()
        assert 3 <= len(default["items"]) <= 20  # limite padrão 20
        walked = await _walk(app_client, limit=2)  # a última página fecha com nextCursor=null
        assert {item["matchId"] for item in walked} >= set(ids)
        assert len({item["matchId"] for item in walked}) == len(walked)
    finally:
        await _delete_matches(session_factory, ids)


@pytest.mark.parametrize("limit", ["0", "101", "-1", "abc"])
async def test_list_rejects_invalid_limit_with_validation_error(app_client, limit) -> None:
    response = await app_client.get("/api/v1/matches", params={"limit": limit})
    _assert_problem(response, 422, "VALIDATION_ERROR")


def _cursor(payload: Any) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


@pytest.mark.parametrize(
    "cursor",
    [
        "lixo-que-nao-e-cursor!",
        "bm90LWpzb24",  # base64 de "not-json"
        _cursor([1, 2, 3]),
        _cursor({"v": 1, "t": "2099-01-01T00:00:00+00:00"}),  # falta "m"
        _cursor({"v": 2, "t": "2099-01-01T00:00:00+00:00", "m": "BR1_1"}),  # versão desconhecida
        _cursor({"v": 1, "t": "2099-01-01T00:00:00", "m": "BR1_1"}),  # sem fuso
        _cursor({"v": 1, "t": "ontem", "m": "BR1_1"}),
        _cursor({"v": 1, "t": "2099-01-01T00:00:00+00:00", "m": "'; DROP TABLE matches;--"}),
        _cursor({"v": 1, "t": 5, "m": "BR1_1"}),
        _cursor({"v": True, "t": "2099-01-01T00:00:00+00:00", "m": "BR1_1"}),
        _cursor({"v": 1, "t": "2099-01-01T00:00:00+00:00", "m": "BR1_1", "x": 1}),
        "A" * 300,
    ],
)
async def test_list_rejects_malformed_cursor_without_echoing_it(app_client, cursor) -> None:
    response = await app_client.get("/api/v1/matches", params={"cursor": cursor})
    _assert_problem(response, 422, "INVALID_CURSOR", echoed=cursor)


# ---------------------------------------------------------------------------
# Detalhe
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("_demo_match", ["DEMO-0002"], indirect=True)
async def test_detail_shows_match_participants_and_preparation_without_personal_data(
    app_client, session_factory, tmp_path, _demo_match
) -> None:
    await import_demo_match(session_factory, tmp_path, "DEMO-0002")
    async with unit_of_work(session_factory) as session:
        hashes = list(
            await session.scalars(
                select(ParticipantModel.subject_hash).where(
                    ParticipantModel.match_id == "DEMO-0002"
                )
            )
        )
    assert hashes and all(hashes)

    response = await app_client.get("/api/v1/matches/DEMO-0002")
    assert response.status_code == 200
    body = response.json()
    assert body["matchId"] == "DEMO-0002" and body["patch"] == "DEMO-1"
    assert body["durationSeconds"] == 1800
    assert body["championsPresent"] == [
        {"championId": p["championId"], "name": None} for p in body["participants"]
    ]
    assert (body["queue"], body["map"]) == (420, 11)
    assert body["preparation"] == {
        "status": "PENDING",
        "errorCode": None,
        "retryAt": None,
        "attempts": 0,
    }
    assert body["participants"], "a partida demo tem participantes"
    for participant in body["participants"]:
        assert set(participant) == {
            "participantId",
            "championId",
            "team",
            "role",
            "champion",
            "win",
            "kills",
            "deaths",
            "assists",
            "totalMinionsKilled",
            "neutralMinionsKilled",
        }
        assert participant["champion"] is None  # DEMO nunca recebe campeão real (CC-208)
    assert not any(word in key.lower() for key in _keys(body) for word in _PERSONAL)
    assert not any(value in response.text for value in hashes)  # o hash nunca sai

    listing = await app_client.get("/api/v1/matches", params={"limit": 100})
    listed = next(item for item in listing.json()["items"] if item["matchId"] == "DEMO-0002")
    assert listed["durationSeconds"] == body["durationSeconds"]
    assert listed["championsPresent"] == body["championsPresent"]
    assert not any(value in listing.text for value in hashes)
    assert not any(word in key.lower() for key in _keys(listing.json()) for word in _PERSONAL)

    raw_store = DurableRawMatchStore(tmp_path / "raw")
    assert await process_next_preparation(session_factory, raw_store, _settings()) is True
    ready = (await app_client.get("/api/v1/matches/DEMO-0002")).json()
    assert ready["preparationStatus"] == "READY" and ready["preparation"]["status"] == "READY"
    row = next(
        item
        for item in (await app_client.get("/api/v1/matches", params={"limit": 100})).json()["items"]
        if item["matchId"] == "DEMO-0002"
    )
    assert row["preparationStatus"] == "READY"


async def test_detail_unknown_match_is_404_and_invalid_id_is_422(app_client) -> None:
    _assert_problem(await app_client.get("/api/v1/matches/BR1_1"), 404, "RESOURCE_NOT_FOUND")
    _assert_problem(await app_client.get("/api/v1/matches/DEMO-9999"), 404, "RESOURCE_NOT_FOUND")
    _assert_problem(await app_client.get("/api/v1/matches/nao-e-id"), 422, "VALIDATION_ERROR")


# ---------------------------------------------------------------------------
# POST /preparation: nova tentativa mínima
# ---------------------------------------------------------------------------


async def test_prepare_unknown_or_never_imported_match_is_404_and_invalid_id_is_422(
    app_client, session_factory
) -> None:
    _assert_problem(
        await app_client.post("/api/v1/matches/BR1_1/preparation"), 404, "RESOURCE_NOT_FOUND"
    )
    _assert_problem(
        await app_client.post("/api/v1/matches/nao-e-id/preparation"), 422, "VALIDATION_ERROR"
    )
    # partida presente sem importação concluída registrada: também não há o que preparar
    match_id = f"BR1_9{uuid.uuid4().int % 10**9:09d}"
    try:
        await _insert_match(session_factory, match_id, _LATE + timedelta(days=40))
        _assert_problem(
            await app_client.post(f"/api/v1/matches/{match_id}/preparation"),
            404,
            "RESOURCE_NOT_FOUND",
        )
    finally:
        await _delete_matches(session_factory, [match_id])


@pytest.mark.parametrize("_demo_match", ["DEMO-0002"], indirect=True)
async def test_prepare_returns_the_active_preparation_and_only_reads_when_ready(
    app_client, session_factory, tmp_path, _demo_match
) -> None:
    await import_demo_match(session_factory, tmp_path, "DEMO-0002")
    first = await app_client.post("/api/v1/matches/DEMO-0002/preparation")
    second = await app_client.post("/api/v1/matches/DEMO-0002/preparation")
    assert first.status_code == 202 and second.status_code == 202
    assert (
        first.json()
        == second.json()
        == {
            "status": "PENDING",
            "errorCode": None,
            "retryAt": None,
            "attempts": 0,
        }
    )
    raw_store = DurableRawMatchStore(tmp_path / "raw")
    assert await process_next_preparation(session_factory, raw_store, _settings()) is True
    ready = await app_client.post("/api/v1/matches/DEMO-0002/preparation")
    assert ready.status_code == 200 and ready.json()["status"] == "READY"


# ---------------------------------------------------------------------------
# E3: nome do item vem do catálogo FIXADO pelo ponto
# ---------------------------------------------------------------------------


class _RenamedDemoCatalog:
    """Mesmos itens do catálogo demo com outro conteúdo/nomes: vira o novo
    canônico de DEMO-1 sem tocar a linha antiga (imutável)."""

    async def load_catalog(self, game_patch: str):
        result = await DemoCatalogSource().load_catalog(game_patch)
        items = tuple(
            dataclasses.replace(
                item, attributes={**item.attributes, "name": f"RENOMEADO {item.item_id}"}
            )
            for item in result.items
        )
        return dataclasses.replace(result, raw_hash=uuid.uuid4().hex * 2, items=items)


@pytest.mark.parametrize("_demo_match", ["DEMO-0002"], indirect=True)
async def test_item_names_come_from_the_catalog_pinned_by_the_point_not_the_current_canonical(
    app_client, session_factory, tmp_path, _demo_match
) -> None:
    await import_demo_match(session_factory, tmp_path, "DEMO-0002")
    raw_store = DurableRawMatchStore(tmp_path / "raw")
    assert await process_next_preparation(session_factory, raw_store, _settings()) is True
    listing = await app_client.get(
        "/api/v1/matches/DEMO-0002/decisions", params={"participantId": 1}
    )
    [point] = listing.json()["items"]
    detail_url = f"/api/v1/decisions/{point['decisionId']}"
    before = (await app_client.get(detail_url)).json()
    pinned_catalog = before["versions"]["catalogId"]
    names_before = {item["itemId"]: item["name"] for item in before["inventory"]["items"]}
    assert names_before == {100001: "Componente Sintético A", 100002: "Componente Sintético B"}

    try:
        swapped = await sync_catalog(session_factory, _RenamedDemoCatalog(), "DEMO-1")
        current = await resolve_catalog_for_match(session_factory, DEMO_1_MAPPING, "DEMO-1")
        assert current is not None and str(current.id) == str(swapped.id) != pinned_catalog

        after = (await app_client.get(detail_url)).json()
        assert after["versions"]["catalogId"] == pinned_catalog
        assert {item["itemId"]: item["name"] for item in after["inventory"]["items"]} == (
            names_before
        )  # o canônico atual (renomeado) nunca aparece
        assert "RENOMEADO" not in json.dumps(after)
    finally:
        await sync_demo_catalog(session_factory)  # restaura o canônico original


async def test_item_name_is_null_without_catalog_or_when_the_catalog_item_has_no_name(
    session_factory, tmp_path, monkeypatch
) -> None:
    from riftlab.catalog.infrastructure.data_dragon_transport import (
        DataDragonTransport,
        RawDataDragonResponse,
    )

    async def versions(_self):
        return RawDataDragonResponse("versions.json", b'["16.19.1"]', "0" * 64)

    monkeypatch.setattr(DataDragonTransport, "fetch_versions", versions)
    unnamed_id = f"BR1_{uuid.uuid4().int % 10**10}"
    no_map_id = f"BR1_{uuid.uuid4().int % 10**10}"
    patch, other_patch = unique_patch(), unique_patch()
    purchases = ((100_000, 3006), (200_000, 3006))  # a 2ª decisão já tem 3006 no inventário
    raw_store = DurableRawMatchStore(tmp_path / "raw")
    try:
        # catálogo sincronizado (stub), mas o item não tem nome
        settings = write_prepare_configs(tmp_path, scope_patch=patch, mapped_patches=(patch,))
        await import_real_match(session_factory, raw_store, unnamed_id, patch, purchases)
        factory = stub_catalog_factory(StubCatalogSource(with_names=False))
        assert await process_next_preparation(session_factory, raw_store, settings, factory)
        assert await _inventory_names(settings, unnamed_id) == {3006: None}

        # sem entrada no mapping: sem catálogo, sem rede, sem nome
        no_map_dir = tmp_path / "sem-mapping"
        no_map_dir.mkdir()
        other = write_prepare_configs(no_map_dir, scope_patch=other_patch, mapped_patches=(patch,))
        await import_real_match(session_factory, raw_store, no_map_id, other_patch, purchases)
        guard = stub_catalog_factory(StubCatalogSource())
        assert await process_next_preparation(session_factory, raw_store, other, guard)
        assert guard.opened == 0
        assert await _inventory_names(other, no_map_id) == {3006: None}
    finally:
        await reset_match(session_factory, unnamed_id)
        await reset_match(session_factory, no_map_id)


async def _inventory_names(settings: Settings, match_id: str) -> dict[int, str | None]:
    """Nomes dos itens do inventário nas decisões da partida (2ª compra em diante)."""
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            listing = await client.get(
                f"/api/v1/matches/{match_id}/decisions", params={"participantId": 1}
            )
            assert listing.status_code == 200, listing.text
            names: dict[int, str | None] = {}
            for point in listing.json()["items"]:
                detail = (await client.get(f"/api/v1/decisions/{point['decisionId']}")).json()
                names.update({i["itemId"]: i["name"] for i in detail["inventory"]["items"]})
            return names


async def test_list_marks_the_local_account_participant_without_exposing_its_hash(
    session_factory, tmp_path
) -> None:
    from riftlab.ingestion.infrastructure.riot_account import RiotAccountConfig
    from riftlab.ranked.domain import account_key
    from riftlab.ranked.models import RankedObservationModel

    account_file = tmp_path / "account.json"
    account_file.write_text(
        json.dumps(
            {
                "gameName": "dono-sintetico",
                "tagLine": "BR1",
                "platform": "BR1",
                "regionalRouting": "AMERICAS",
            }
        )
    )
    settings = Settings(ranked_account_file=str(account_file), subject_hash_secret="segredo-x")
    key = account_key(RiotAccountConfig("dono-sintetico", "BR1", "BR1", "AMERICAS"), "segredo-x")
    own_hash, other_hash = "a" * 64, "b" * 64
    match_id = f"BR1_7{uuid.uuid4().int % 10**9:09d}"
    try:
        await _insert_match(session_factory, match_id, _LATE + timedelta(days=400))
        async with unit_of_work(session_factory) as session:
            for pid in range(1, 11):
                session.add(
                    ParticipantModel(
                        match_id=match_id,
                        participant_id=pid,
                        champion_id=100 + pid,
                        team=100 if pid <= 5 else 200,
                        role="BOTTOM" if pid in (4, 9) else None,
                        subject_hash=own_hash if pid == 4 else f"{other_hash[:-2]}{pid:02d}",
                        win=pid <= 5,
                        kills=pid,
                        deaths=1,
                        assists=2,
                        total_minions_killed=150,
                        neutral_minions_killed=10,
                    )
                )
            session.add(
                RankedObservationModel(
                    account_key=key,
                    subject_hash=own_hash,
                    queue="RANKED_FLEX_SR",
                    season="2026-3",
                    captured_at=datetime.now(UTC),
                    state="READY",
                    origin="RIOT_LEAGUE_V4",
                )
            )
        app = create_app(settings)
        async with app.router.lifespan_context(app):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.get("/api/v1/matches", params={"limit": 100})
        assert response.status_code == 200
        item = next(i for i in response.json()["items"] if i["matchId"] == match_id)
        assert item["localParticipantId"] == 4
        assert [p["participantId"] for p in item["participants"]] == list(range(1, 11))
        own = item["participants"][3]
        assert (own["win"], own["kills"], own["team"], own["role"]) == (True, 4, 100, "BOTTOM")
        assert own_hash not in response.text and other_hash[:-2] not in response.text
        assert not any(word in k.lower() for k in _keys(response.json()) for word in _PERSONAL)

        async with AsyncClient(
            transport=ASGITransport(app=create_app(Settings())), base_url="http://test"
        ) as anonymous:
            app2 = anonymous._transport.app  # type: ignore[attr-defined]
            async with app2.router.lifespan_context(app2):
                plain = await anonymous.get("/api/v1/matches", params={"limit": 100})
        listed = next(i for i in plain.json()["items"] if i["matchId"] == match_id)
        assert listed["localParticipantId"] is None  # sem conta configurada: ninguém é marcado
    finally:
        async with unit_of_work(session_factory) as session:
            await session.execute(
                RankedObservationModel.__table__.delete().where(
                    RankedObservationModel.account_key == key
                )
            )
            await session.execute(
                ParticipantModel.__table__.delete().where(ParticipantModel.match_id == match_id)
            )
        await _delete_matches(session_factory, [match_id])
