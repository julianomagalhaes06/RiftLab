"""CC-1216: partida RIOT expurgada não volta por nenhum caminho de importação (PostgreSQL real,
bruto local sintético, sem rede)."""

from __future__ import annotations

import hashlib
import json
import uuid

import pytest
from pipeline_helpers import purge_open_preparations, reset_match
from sqlalchemy import delete, select, update

from riftlab.collection.importing import enqueue_collected_imports
from riftlab.config import Settings
from riftlab.ingestion import application as ingestion_application
from riftlab.ingestion.application import (
    process_next_job,
    raw_pair_available,
    request_import,
    retry_import,
)
from riftlab.ingestion.domain import ImportJobState, ImportMode
from riftlab.ingestion.infrastructure.raw_store import DurableRawMatchStore
from riftlab.ingestion.models import ImportJobModel, MatchModel, PurgeTombstoneModel
from riftlab.ingestion.purge import purge_match
from riftlab.ingestion.tombstone import MatchPurgedError, match_ref, scrubbed_match_id
from riftlab.shared.db import unit_of_work
from support.riot_stub import HASH_SECRET, real_pair

SECRET = HASH_SECRET


@pytest.fixture
def match_id() -> str:
    return f"BR1_{uuid.uuid4().int % 10**10}"


@pytest.fixture
def raw_store(tmp_path) -> DurableRawMatchStore:
    return DurableRawMatchStore(tmp_path / "raw")


@pytest.fixture
async def clean(session_factory, match_id):
    async def wipe() -> None:
        await reset_match(session_factory, match_id)
        async with unit_of_work(session_factory) as session:
            await session.execute(
                delete(ImportJobModel).where(
                    ImportJobModel.requested_match_id.in_(
                        [match_id, scrubbed_match_id(match_id, SECRET)]
                    )
                )
            )
            await session.execute(
                delete(PurgeTombstoneModel).where(
                    PurgeTombstoneModel.match_ref == match_ref(match_id, SECRET)
                )
            )
        await purge_open_preparations(session_factory)

    await wipe()
    yield
    await wipe()


def _settings() -> Settings:
    return Settings(subject_hash_secret=SECRET)


def _store_pair(raw_store: DurableRawMatchStore, match_id: str) -> None:
    for kind, doc in zip(
        ("match", "timeline"), real_pair(match_id, seed_puuid="puuid-sintetico"), strict=True
    ):
        body = json.dumps(doc).encode()
        raw_store.store(match_id, kind, body, hashlib.sha256(body).hexdigest())


async def _tombstone(session_factory, match_id: str) -> None:
    async with unit_of_work(session_factory) as session:
        session.add(
            PurgeTombstoneModel(
                match_ref=match_ref(match_id, SECRET),
                mode="RIOT",
                reason="USER_REQUEST",
                removed={},
            )
        )


async def _jobs(session_factory, *ids: str) -> list[ImportJobModel]:
    async with session_factory() as session:
        rows = await session.scalars(
            select(ImportJobModel).where(
                ImportJobModel.requested_match_id.in_(ids), ImportJobModel.kind == "IMPORT"
            )
        )
        return list(rows.all())


async def _match_exists(session_factory, match_id: str) -> bool:
    async with session_factory() as session:
        return await session.get(MatchModel, match_id) is not None


async def _assert_dropped(session_factory, raw_store, match_id: str) -> None:
    """Sem partida, bruto fora e o job FAILED MATCH_PURGED sem o id real."""
    assert not await _match_exists(session_factory, match_id)
    assert not raw_pair_available(raw_store, match_id)
    assert await _jobs(session_factory, match_id) == []
    [job] = await _jobs(session_factory, scrubbed_match_id(match_id, SECRET))
    assert (job.state, job.error_code) == (ImportJobState.FAILED.value, "MATCH_PURGED")


@pytest.mark.asyncio
async def test_job_creation_and_retry_refuse_a_purged_match(session_factory, match_id, clean):
    created = await request_import(session_factory, match_id, ImportMode.RIOT, secret=SECRET)
    async with unit_of_work(session_factory) as session:
        await session.execute(
            update(ImportJobModel)
            .where(ImportJobModel.id == created.id)
            .values(state=ImportJobState.FAILED.value, error_code="UPSTREAM_UNAVAILABLE")
        )
    await _tombstone(session_factory, match_id)

    with pytest.raises(MatchPurgedError):
        await request_import(session_factory, match_id, ImportMode.RIOT, secret=SECRET)
    with pytest.raises(MatchPurgedError):
        await retry_import(session_factory, created.id, secret=SECRET)
    [only] = await _jobs(session_factory, match_id)
    assert only.id == created.id  # nenhuma tentativa nova


@pytest.mark.asyncio
async def test_purge_committed_between_the_route_check_and_the_job_is_seen(
    session_factory, raw_store, match_id, clean
):
    # A janela do achado: nada expurgado na leitura inicial, expurgo concluído antes do INSERT.
    async def purge_now() -> None:
        await purge_match(
            session_factory,
            raw_store,
            match_id,
            mode=ImportMode.RIOT,
            reason="USER_REQUEST",
            secret=SECRET,
        )

    with pytest.raises(MatchPurgedError):
        await request_import(session_factory, match_id, ImportMode.RIOT, purge_now, secret=SECRET)
    assert await _jobs(session_factory, match_id) == []


@pytest.mark.asyncio
async def test_collected_raw_of_a_purged_match_never_becomes_a_job(
    session_factory, raw_store, match_id, clean
):
    _store_pair(raw_store, match_id)  # nova coleta (ou restauração) trouxe o bruto de volta
    await _tombstone(session_factory, match_id)

    report = await enqueue_collected_imports(session_factory, raw_store, [match_id], secret=SECRET)

    assert (report.enqueued, report.already_requested, report.purged) == (0, 0, 1)
    assert not raw_pair_available(raw_store, match_id)
    assert await _jobs(session_factory, match_id) == []


@pytest.mark.asyncio
async def test_worker_drops_a_job_queued_before_the_purge(
    session_factory, raw_store, match_id, clean
):
    await request_import(session_factory, match_id, ImportMode.RIOT, secret=SECRET)
    _store_pair(raw_store, match_id)
    await _tombstone(session_factory, match_id)

    assert await process_next_job(session_factory, raw_store, _settings(), local_only=True)

    await _assert_dropped(session_factory, raw_store, match_id)


@pytest.mark.asyncio
async def test_purge_during_the_fetch_never_recreates_the_match(
    session_factory, raw_store, match_id, clean, monkeypatch
):
    await request_import(session_factory, match_id, ImportMode.RIOT, secret=SECRET)
    _store_pair(raw_store, match_id)
    load = ingestion_application._load_from_source

    async def load_then_purge(*args, **kwargs):
        result = await load(*args, **kwargs)
        await _tombstone(session_factory, match_id)  # expurgo concluído enquanto o worker buscava
        return result

    monkeypatch.setattr(ingestion_application, "_load_from_source", load_then_purge)

    assert await process_next_job(session_factory, raw_store, _settings(), local_only=True)

    await _assert_dropped(session_factory, raw_store, match_id)


@pytest.mark.asyncio
async def test_without_a_tombstone_the_local_import_still_succeeds(
    session_factory, raw_store, match_id, clean
):
    await request_import(session_factory, match_id, ImportMode.RIOT, secret=SECRET)
    _store_pair(raw_store, match_id)

    assert await process_next_job(session_factory, raw_store, _settings(), local_only=True)

    assert await _match_exists(session_factory, match_id)
    [job] = await _jobs(session_factory, match_id)
    assert job.state == ImportJobState.SUCCEEDED.value
