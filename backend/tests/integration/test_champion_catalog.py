"""Catálogo de campeões (CC-208) em PostgreSQL isolado: sincronização idempotente com
rede só quando há mapping, leitura só de banco nas rotas GET, DEMO sem atribuição
falsa e preparação que continua pronta quando o CDN falha. Nenhuma rede real: a
busca é um stub com o recorte REAL do champion.json (tests/support/data_dragon/)."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from pipeline_helpers import (
    StubCatalogSource,
    import_demo_match,
    import_real_match,
    purge_open_preparations,
    stub_catalog_factory,
    write_prepare_configs,
)
from sqlalchemy import delete, func, select

from riftlab.api.app import create_app
from riftlab.catalog.infrastructure.catalog_mapping import (
    load_catalog_mapping,
    parse_catalog_mapping_document,
)
from riftlab.catalog.infrastructure.data_dragon_transport import RawDataDragonResponse
from riftlab.champions.application import (
    ChampionSyncOutcome,
    champion_assets,
    ensure_champion_catalog,
)
from riftlab.champions.models import ChampionCatalogModel, ChampionModel
from riftlab.ingestion.infrastructure.raw_store import DurableRawMatchStore
from riftlab.preparation.application import process_next_preparation
from riftlab.shared.db import read_only_unit_of_work, unit_of_work
from riftlab.shared.errors import SourceError, SourceSchemaError

from pipeline_helpers import reset_match  # isort: skip

_FIXTURES = Path(__file__).resolve().parents[1] / "support" / "data_dragon"
_PINNED = parse_catalog_mapping_document(
    b'schemaVersion: v1\nmappingVersion: v1\nmappings:\n  "16.16": {dataDragonVersion: "16.16.1"}\n'
    b'  "16.17": {dataDragonVersion: "16.17.1"}\n'
)


def _champion_patch() -> str:
    """Patch sintético único DENTRO do formato aceito pelo Data Dragon (x.y → x.y.1,
    até 3 dígitos por parte); o `unique_patch` dos outros testes gera major longo."""
    n = uuid.uuid4().int
    return f"{n % 800 + 100}.{n // 800 % 90 + 10}"


def _real_doc(version: str) -> dict:
    return json.loads((_FIXTURES / f"champion_{version}_pt_BR_subset.json").read_bytes())


class FakeFetch:
    """Busca de champion.json sem rede; conta as chamadas."""

    def __init__(self, bodies: dict[str, bytes] | None = None, error: Exception | None = None):
        self.bodies = bodies or {}
        self.error = error
        self.calls: list[tuple[str, str]] = []

    async def __call__(self, version: str, locale: str) -> RawDataDragonResponse:
        self.calls.append((version, locale))
        if self.error is not None:
            raise self.error
        body = self.bodies[version]
        return RawDataDragonResponse(version, body, hashlib.sha256(body).hexdigest())


def _synthetic_body(version: str, *, drop: tuple[str, ...] = ()) -> bytes:
    """Recorte real reescrito para a versão sintética do teste (patch único)."""
    doc = _real_doc("16.17.1")
    doc["version"] = version
    doc["data"] = {k: v for k, v in doc["data"].items() if v["key"] not in drop}
    return json.dumps(doc).encode()


async def _purge_versions(session_factory, versions: list[str]) -> None:
    async with unit_of_work(session_factory) as session:
        ids = list(
            await session.scalars(
                select(ChampionCatalogModel.id).where(
                    ChampionCatalogModel.data_dragon_version.in_(versions)
                )
            )
        )
        if ids:
            await session.execute(delete(ChampionModel).where(ChampionModel.catalog_id.in_(ids)))
            await session.execute(
                delete(ChampionCatalogModel).where(ChampionCatalogModel.id.in_(ids))
            )


@pytest_asyncio.fixture(autouse=True)
async def _isolated_preparation_queue(session_factory) -> AsyncIterator[None]:
    """Mesma higiene de test_preparation.py: nenhuma preparação pendente de outro
    teste é apanhada pelo worker destes testes."""
    await purge_open_preparations(session_factory)
    yield
    await purge_open_preparations(session_factory)


@pytest_asyncio.fixture
async def pinned_versions_clean(session_factory) -> AsyncIterator[None]:
    await _purge_versions(session_factory, ["16.16.1", "16.17.1"])
    yield
    await _purge_versions(session_factory, ["16.16.1", "16.17.1"])


@pytest_asyncio.fixture
async def real_match(session_factory) -> AsyncIterator[str]:
    match_id = f"BR1_{uuid.uuid4().int % 10**10}"
    try:
        yield match_id
    finally:
        await reset_match(session_factory, match_id)


@asynccontextmanager
async def _client(settings) -> AsyncIterator[AsyncClient]:
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client


def _champions_by_participant(body: dict) -> dict[int, dict | None]:
    return {p["participantId"]: p["champion"] for p in body["participants"]}


async def test_patch_without_mapping_uses_no_network(session_factory) -> None:
    fetch = FakeFetch()
    outcome = await ensure_champion_catalog(session_factory, _PINNED, "99.1", fetch)
    assert outcome is ChampionSyncOutcome.NO_MAPPING and fetch.calls == []


async def test_both_pinned_patches_sync_once_and_resolve_the_recorte_by_numeric_key(
    session_factory, pinned_versions_clean
) -> None:
    fetch = FakeFetch({v: json.dumps(_real_doc(v)).encode() for v in ("16.16.1", "16.17.1")})
    assert (
        await ensure_champion_catalog(session_factory, _PINNED, "16.16", fetch)
        is ChampionSyncOutcome.SYNCED
    )
    assert (
        await ensure_champion_catalog(session_factory, _PINNED, "16.17", fetch)
        is ChampionSyncOutcome.SYNCED
    )
    # cache durável por (versão, idioma): nenhuma nova busca
    assert (
        await ensure_champion_catalog(session_factory, _PINNED, "16.17", fetch)
        is ChampionSyncOutcome.CACHED
    )
    assert fetch.calls == [("16.16.1", "pt_BR"), ("16.17.1", "pt_BR")]

    async with read_only_unit_of_work(session_factory) as session:
        for patch, version in (("16.16", "16.16.1"), ("16.17", "16.17.1")):
            assets = await champion_assets(session, _PINNED, "BR1_1", patch, [145, 51, 81, 62, 999])
            assert {
                k: (a.name, a.image_file, a.data_dragon_version) for k, a in assets.items()
            } == {
                145: ("Kai'Sa", "Kaisa.png", version),
                51: ("Caitlyn", "Caitlyn.png", version),
                81: ("Ezreal", "Ezreal.png", version),
                62: ("Wukong", "MonkeyKing.png", version),  # key 62, id textual "MonkeyKing"
            }  # 999: id desconhecido, sem atribuição
        # DEMO nunca recebe campeão real, mesmo com ids e catálogo que existiriam
        assert (
            await champion_assets(session, _PINNED, "DEMO-0002", "16.17", [145, 9001, 9002]) == {}
        )


async def test_version_divergent_document_persists_nothing(
    session_factory, pinned_versions_clean
) -> None:
    fetch = FakeFetch({"16.17.1": json.dumps(_real_doc("16.16.1")).encode()})
    with pytest.raises(SourceSchemaError):
        await ensure_champion_catalog(session_factory, _PINNED, "16.17", fetch)
    async with read_only_unit_of_work(session_factory) as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(ChampionCatalogModel)
                .where(ChampionCatalogModel.data_dragon_version == "16.17.1")
            )
            == 0
        )


async def test_match_detail_reads_champions_from_the_database_only(
    session_factory, tmp_path, real_match
) -> None:
    patch = _champion_patch()
    version = f"{patch}.1"
    settings = write_prepare_configs(tmp_path, scope_patch=patch, mapped_patches=(patch,))
    raw_store = DurableRawMatchStore(tmp_path / "raw")
    await import_real_match(session_factory, raw_store, real_match, patch, ((120_000, 3006),))
    try:
        async with _client(settings) as client:
            before = (await client.get(f"/api/v1/matches/{real_match}")).json()
            assert set(_champions_by_participant(before).values()) == {None}  # ainda sem catálogo

            mapping = load_catalog_mapping(tmp_path / "catalog-mappings.yaml")
            fetch = FakeFetch({version: _synthetic_body(version, drop=("89",))})
            await ensure_champion_catalog(session_factory, mapping, patch, fetch)
            after = (await client.get(f"/api/v1/matches/{real_match}")).json()
            by_champion = {p["championId"]: p["champion"] for p in after["participants"]}
            assert by_champion[51] == {
                "name": "Caitlyn",
                "dataDragonVersion": version,
                "imageFile": "Caitlyn.png",
            }
            assert by_champion[89] is None  # ausente do catálogo: rótulo numérico na interface
            assert len(fetch.calls) == 1  # os GET não buscaram nada
    finally:
        await _purge_versions(session_factory, [version])


@pytest.mark.parametrize("_demo_match", ["DEMO-0002"], indirect=True)
async def test_demo_match_detail_never_gets_a_real_champion(
    session_factory, tmp_path, _demo_match, app_client
) -> None:
    await import_demo_match(session_factory, tmp_path, "DEMO-0002")
    body = (await app_client.get("/api/v1/matches/DEMO-0002")).json()
    assert body["participants"] and {p["champion"] for p in body["participants"]} == {None}
    assert {p["championId"] for p in body["participants"]} >= {9001}


async def test_preparation_syncs_champions_and_a_cdn_failure_keeps_the_match_ready(
    session_factory, tmp_path, real_match
) -> None:
    patch = _champion_patch()
    version = f"{patch}.1"
    settings = write_prepare_configs(tmp_path, scope_patch=patch, mapped_patches=(patch,))
    raw_store = DurableRawMatchStore(tmp_path / "raw")
    other = f"BR1_{uuid.uuid4().int % 10**10}"
    try:
        # CDN fora do ar: a partida fica pronta, só sem nomes/retratos
        await import_real_match(session_factory, raw_store, real_match, patch, ((120_000, 3006),))
        down = FakeFetch(error=SourceError(code="UPSTREAM_UNAVAILABLE", detail="cdn fora"))
        factory = stub_catalog_factory(StubCatalogSource())
        assert await process_next_preparation(
            session_factory, raw_store, settings, factory, champion_fetch=down
        )
        async with _client(settings) as client:
            body = (await client.get(f"/api/v1/matches/{real_match}")).json()
            assert body["preparation"]["status"] == "READY"
            assert {p["champion"] is None for p in body["participants"]} == {True}
        assert down.calls == [(version, "pt_BR")]

        # CDN de volta: a preparação da próxima partida sincroniza o catálogo
        await import_real_match(session_factory, raw_store, other, patch, ((120_000, 3006),))
        up = FakeFetch({version: _synthetic_body(version)})
        assert await process_next_preparation(
            session_factory, raw_store, settings, factory, champion_fetch=up
        )
        async with _client(settings) as client:
            body = (await client.get(f"/api/v1/matches/{other}")).json()
            names = {
                p["championId"]: (p["champion"] or {}).get("name") for p in body["participants"]
            }
            assert names[51] == "Caitlyn" and names[89] == "Leona"
    finally:
        await reset_match(session_factory, other)
        await _purge_versions(session_factory, [version])
