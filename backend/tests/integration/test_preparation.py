"""Preparação durável de partida (CC-205) contra PostgreSQL real: import ->
catálogo -> reconstrução v6 -> extração, sem console. Jobs `kind=PREPARE` na
mesma maquinaria de lease/token/retry do CC-201. Modo REAL só com transporte
simulado e catálogo stub: nenhum teste toca a rede."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
import yaml
from httpx import ASGITransport, AsyncClient
from pipeline_helpers import (
    FAKE_SUBJECT_HASH_SECRET,
    StubCatalogSource,
    import_demo_match,
    import_real_match,
    purge_open_preparations,
    stub_catalog_factory,
    unique_patch,
    write_prepare_configs,
)
from sqlalchemy import delete, func, select, update

from riftlab.api.app import create_app
from riftlab.catalog.application import resolve_catalog_for_match, sync_catalog
from riftlab.catalog.infrastructure.catalog_mapping import load_catalog_mapping
from riftlab.catalog.infrastructure.curation import CURATION_RULES_VERSION
from riftlab.catalog.models import CatalogVersionModel
from riftlab.config import Settings
from riftlab.decisions.models import DecisionExtractionModel, DecisionPointModel
from riftlab.ingestion.domain import (
    MAX_TRANSIENT_ATTEMPTS,
    ImportJob,
    ImportJobState,
    ImportMode,
    JobKind,
)
from riftlab.ingestion.infrastructure.raw_store import DurableRawMatchStore
from riftlab.ingestion.models import ImportJobModel, MatchModel
from riftlab.ingestion.repository import SqlAlchemyIngestionRepository
from riftlab.preparation import application as preparation
from riftlab.preparation.application import enqueue_idle_preparation, process_next_preparation
from riftlab.shared.db import unit_of_work
from riftlab.shared.errors import SourceError, SourceRateLimitedError
from riftlab.timeline.application import RECONSTRUCTION_ALGORITHM_VERSION
from riftlab.timeline.domain import TimelineEvent
from riftlab.timeline.empirical_rules import KNOWN_RULE_IDS
from riftlab.timeline.models import MatchReconstructionModel, TimelineEventModel
from riftlab.timeline.repository import SqlAlchemyTimelineRepository

from pipeline_helpers import reset_match  # isort: skip


@pytest_asyncio.fixture(autouse=True)
async def _isolated_preparation_queue(session_factory) -> AsyncIterator[None]:
    await purge_open_preparations(session_factory)
    yield
    await purge_open_preparations(session_factory)


@pytest_asyncio.fixture
async def real_match(session_factory) -> AsyncIterator[str]:
    match_id = f"BR1_{uuid.uuid4().int % 10**10}"
    try:
        yield match_id
    finally:
        await reset_match(session_factory, match_id)


@asynccontextmanager
async def _client(settings: Settings) -> AsyncIterator[AsyncClient]:
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://127.0.0.1"
        ) as client:
            yield client


async def _jobs(session_factory, match_id: str) -> list[ImportJobModel]:
    async with unit_of_work(session_factory) as session:
        rows = await session.scalars(
            select(ImportJobModel)
            .where(ImportJobModel.requested_match_id == match_id, ImportJobModel.kind == "PREPARE")
            .order_by(ImportJobModel.created_at, ImportJobModel.id)
        )
        return list(rows)


async def _counts(session_factory, match_id: str) -> tuple[int, int, int]:
    """(marcas de reconstrução, extrações, pontos) da partida."""
    async with unit_of_work(session_factory) as session:
        values = [
            await session.scalar(
                select(func.count()).select_from(model).where(model.match_id == match_id)
            )
            for model in (MatchReconstructionModel, DecisionExtractionModel, DecisionPointModel)
        ]
    return values[0] or 0, values[1] or 0, values[2] or 0


async def _expire_lease(session_factory, job_id: uuid.UUID) -> None:
    async with unit_of_work(session_factory) as session:
        await session.execute(
            update(ImportJobModel)
            .where(ImportJobModel.id == job_id)
            .values(lease_until=datetime.now(UTC) - timedelta(seconds=5))
        )


async def _release_retry(session_factory, job_id: uuid.UUID) -> None:
    async with unit_of_work(session_factory) as session:
        await session.execute(
            update(ImportJobModel)
            .where(ImportJobModel.id == job_id)
            .values(retry_at=datetime.now(UTC) - timedelta(seconds=5))
        )


def _demo_settings() -> Settings:
    return Settings(subject_hash_secret=FAKE_SUBJECT_HASH_SECRET)


def _decisions(match_id: str) -> str:
    return f"/api/v1/matches/{match_id}/decisions"


# ---------------------------------------------------------------------------
# Import DEMO -> READY sem console
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("_demo_match", ["DEMO-0002"], indirect=True)
async def test_demo_import_enqueues_and_worker_reaches_ready_with_points(
    app_client, session_factory, tmp_path, _demo_match
) -> None:
    await import_demo_match(session_factory, tmp_path, "DEMO-0002")

    [job] = await _jobs(session_factory, "DEMO-0002")  # enfileirada pelo próprio import
    assert job.kind == "PREPARE" and job.state == "QUEUED"
    pending = (await app_client.get("/api/v1/matches/DEMO-0002")).json()
    assert pending["preparationStatus"] == "PENDING"
    assert pending["preparation"]["status"] == "PENDING"
    not_prepared = await app_client.get(_decisions("DEMO-0002"), params={"participantId": 1})
    assert not_prepared.status_code == 409  # distinto de "extraída com zero pontos"

    raw_store = DurableRawMatchStore(tmp_path / "raw")
    assert await process_next_preparation(session_factory, raw_store, _demo_settings()) is True

    [job] = await _jobs(session_factory, "DEMO-0002")
    assert (job.state, job.attempts, job.error_code) == ("SUCCEEDED", 1, None)
    ready = (await app_client.get("/api/v1/matches/DEMO-0002")).json()
    assert ready["preparationStatus"] == "READY" and ready["preparation"]["attempts"] == 1
    listing = await app_client.get(_decisions("DEMO-0002"), params={"participantId": 1})
    assert listing.status_code == 200
    [point] = listing.json()["items"]
    assert point["eligible"] is True
    # nada elegível depois: a fila de preparação esvaziou
    assert await process_next_preparation(session_factory, raw_store, _demo_settings()) is False


@pytest.mark.parametrize("_demo_match", ["DEMO-0008"], indirect=True)
async def test_rerun_after_failure_is_idempotent_and_never_duplicates(
    app_client, session_factory, tmp_path, _demo_match
) -> None:
    await import_demo_match(session_factory, tmp_path, "DEMO-0008")
    raw_store = DurableRawMatchStore(tmp_path / "raw")
    assert await process_next_preparation(session_factory, raw_store, _demo_settings()) is True
    before = await _counts(session_factory, "DEMO-0008")
    assert before[0] == 1 and before[1] == 1 and before[2] > 0

    # READY e versões correntes: pedir de novo devolve a mesma preparação
    again = await app_client.post("/api/v1/matches/DEMO-0008/preparation")
    assert again.status_code == 200 and again.json()["status"] == "READY"
    assert len(await _jobs(session_factory, "DEMO-0008")) == 1

    # uma falha registrada + nova tentativa: refaz sem duplicar nada (marcas idempotentes)
    [first] = await _jobs(session_factory, "DEMO-0008")
    async with unit_of_work(session_factory) as session:
        await session.execute(
            update(ImportJobModel)
            .where(ImportJobModel.id == first.id)
            .values(state="FAILED", error_code="TEST_INDUCED", match_id=None)
        )
    retry = await app_client.post("/api/v1/matches/DEMO-0008/preparation")
    assert retry.status_code == 202 and retry.json()["status"] == "PENDING"
    assert await process_next_preparation(session_factory, raw_store, _demo_settings()) is True

    jobs = await _jobs(session_factory, "DEMO-0008")
    assert [j.state for j in jobs] == ["FAILED", "SUCCEEDED"]  # histórico preservado
    assert jobs[1].retry_of_job_id == first.id
    assert await _counts(session_factory, "DEMO-0008") == before


# ---------------------------------------------------------------------------
# Crash no meio e retomada (lease vencido)
# ---------------------------------------------------------------------------


class _SimulatedCrash(BaseException):
    """Passa por `except Exception`: o processo "morre" sem finalizar o job."""


@pytest.mark.parametrize("_demo_match", ["DEMO-0008"], indirect=True)
async def test_crash_mid_preparation_resumes_after_lease_expiry_without_duplicating(
    session_factory, tmp_path, monkeypatch, _demo_match
) -> None:
    await import_demo_match(session_factory, tmp_path, "DEMO-0008")
    raw_store = DurableRawMatchStore(tmp_path / "raw")

    async def die_before_extraction(*_args: Any, **_kwargs: Any) -> None:
        raise _SimulatedCrash

    with monkeypatch.context() as patched:
        patched.setattr(preparation, "extract_decisions", die_before_extraction)
        with pytest.raises(_SimulatedCrash):
            await process_next_preparation(session_factory, raw_store, _demo_settings())

    [crashed] = await _jobs(session_factory, "DEMO-0008")
    assert (crashed.state, crashed.attempts, crashed.lease_generation) == ("RUNNING", 1, 1)
    assert await _counts(session_factory, "DEMO-0008") == (1, 0, 0)  # reconstruiu, não extraiu
    # posse ainda vigente: nenhum outro ciclo pode pegá-la
    assert await process_next_preparation(session_factory, raw_store, _demo_settings()) is False

    async with unit_of_work(session_factory) as session:
        stale = await SqlAlchemyIngestionRepository(session).get_latest_preparation_job("DEMO-0008")
    assert stale is not None
    await _expire_lease(session_factory, crashed.id)
    assert await process_next_preparation(session_factory, raw_store, _demo_settings()) is True

    [resumed] = await _jobs(session_factory, "DEMO-0008")
    assert (resumed.state, resumed.attempts, resumed.lease_generation) == ("SUCCEEDED", 2, 2)
    marks, extractions, points = await _counts(session_factory, "DEMO-0008")
    assert (marks, extractions) == (1, 1) and points > 0  # nada duplicado

    # o dono antigo que acorda depois não consegue sobrescrever o resultado
    await preparation._finalize(session_factory, stale, failed="TOO_LATE")
    [after] = await _jobs(session_factory, "DEMO-0008")
    assert (after.state, after.error_code) == ("SUCCEEDED", None)


# ---------------------------------------------------------------------------
# Catálogo em modo REAL: rede só com mapping explícito
# ---------------------------------------------------------------------------


async def test_real_patch_without_mapping_entry_uses_no_network_and_marks_missing_catalog(
    session_factory, tmp_path, real_match, monkeypatch
) -> None:
    from riftlab.catalog.infrastructure.data_dragon_transport import (
        DataDragonTransport,
        RawDataDragonResponse,
    )

    async def versions(_self):
        return RawDataDragonResponse("versions.json", b'["16.19.1"]', "0" * 64)

    monkeypatch.setattr(DataDragonTransport, "fetch_versions", versions)
    patch = unique_patch()
    settings = write_prepare_configs(tmp_path, scope_patch=patch, mapped_patches=("1.1",))
    raw_store = DurableRawMatchStore(tmp_path / "raw")
    await import_real_match(session_factory, raw_store, real_match, patch, ((120_000, 3006),))

    factory = stub_catalog_factory(StubCatalogSource())
    assert await process_next_preparation(session_factory, raw_store, settings, factory) is True
    assert factory.opened == 0  # nenhuma fonte de catálogo (nenhuma rede) foi sequer aberta

    [job] = await _jobs(session_factory, real_match)
    assert (job.state, job.error_code) == ("SUCCEEDED", None)
    async with _client(settings) as client:
        listing = await client.get(_decisions(real_match), params={"participantId": 1})
        assert listing.status_code == 200
        [point] = listing.json()["items"]
        assert point["eligible"] is False and "MISSING_CATALOG" in point["reasonCodes"]
        detail = (await client.get(f"/api/v1/decisions/{point['decisionId']}")).json()
        assert detail["versions"]["catalogId"] is None
        assert all(item["name"] is None for item in detail["inventory"]["items"])


async def test_real_patch_with_mapping_syncs_catalog_once_through_stub(
    session_factory, tmp_path, real_match
) -> None:
    patch = unique_patch()
    settings = write_prepare_configs(tmp_path, scope_patch=patch, mapped_patches=(patch,))
    raw_store = DurableRawMatchStore(tmp_path / "raw")
    await import_real_match(session_factory, raw_store, real_match, patch, ((120_000, 3006),))
    mapping = load_catalog_mapping(tmp_path / "catalog-mappings.yaml")
    assert await resolve_catalog_for_match(session_factory, mapping, patch) is None

    stub = StubCatalogSource()
    factory = stub_catalog_factory(stub)
    assert await process_next_preparation(session_factory, raw_store, settings, factory) is True
    assert stub.loads == [patch] and factory.opened == 1
    canonical = await resolve_catalog_for_match(session_factory, mapping, patch)
    assert canonical is not None

    async with _client(settings) as client:
        listing = await client.get(_decisions(real_match), params={"participantId": 1})
        assert listing.status_code == 200
        [point] = listing.json()["items"]
        assert "MISSING_CATALOG" not in point["reasonCodes"]
        detail = (await client.get(f"/api/v1/decisions/{point['decisionId']}")).json()
        assert detail["versions"]["catalogId"] == str(canonical.id)

    # outra partida do mesmo patch: o canônico já existe, nenhuma nova sincronização
    other = f"BR1_{uuid.uuid4().int % 10**10}"
    try:
        await import_real_match(session_factory, raw_store, other, patch, ((120_000, 3006),))
        assert await process_next_preparation(session_factory, raw_store, settings, factory) is True
        assert stub.loads == [patch] and factory.opened == 1
    finally:
        await reset_match(session_factory, other)


async def test_canonical_catalog_with_a_non_current_curation_is_resynced_not_reused_silently(
    session_factory, tmp_path, real_match
) -> None:
    """Achado da coleta real (CC-206, rodada 2a): um canônico sincronizado ANTES da curadoria
    corrente (ex.: LEGACY_UNKNOWN, do CC-202) era tratado como suficiente e o classificador
    rejeitava todos os itens em silêncio. Agora a preparação ressincroniza."""
    patch = unique_patch()
    settings = write_prepare_configs(tmp_path, scope_patch=patch, mapped_patches=(patch,))
    raw_store = DurableRawMatchStore(tmp_path / "raw")
    await import_real_match(session_factory, raw_store, real_match, patch, ((120_000, 3006),))
    mapping = load_catalog_mapping(tmp_path / "catalog-mappings.yaml")
    legacy = await sync_catalog(
        session_factory,
        StubCatalogSource(curation_rules_version="LEGACY_UNKNOWN"),
        patch,
    )
    canonical = await resolve_catalog_for_match(session_factory, mapping, patch)
    assert canonical is not None and canonical.id == legacy.id  # o legado é o canônico

    stub = StubCatalogSource()
    factory = stub_catalog_factory(stub)
    assert await process_next_preparation(session_factory, raw_store, settings, factory) is True

    assert stub.loads == [patch] and factory.opened == 1  # ressincronizou (rede só do catálogo)
    current = await resolve_catalog_for_match(session_factory, mapping, patch)
    assert current is not None and current.id != legacy.id
    assert current.curation_rules_version == CURATION_RULES_VERSION
    async with unit_of_work(session_factory) as session:
        versions = list(
            await session.scalars(
                select(CatalogVersionModel.curation_rules_version).where(
                    CatalogVersionModel.game_patch == patch
                )
            )
        )
        points = list(
            await session.scalars(
                select(DecisionPointModel).where(DecisionPointModel.match_id == real_match)
            )
        )
    assert sorted(versions) == sorted(
        ["LEGACY_UNKNOWN", CURATION_RULES_VERSION]
    )  # legado preservado
    assert points and all(p.curation_rules_version == CURATION_RULES_VERSION for p in points)
    assert all(
        "CLASSIFICATION_UNSUPPORTED_CURATION" not in str(p.reasons) for p in points
    )  # o alvo agora é classificável

    other = f"BR1_{uuid.uuid4().int % 10**10}"
    try:  # e a partida seguinte do mesmo patch NÃO ressincroniza
        await import_real_match(session_factory, raw_store, other, patch, ((120_000, 3006),))
        assert await process_next_preparation(session_factory, raw_store, settings, factory) is True
        assert stub.loads == [patch] and factory.opened == 1
    finally:
        await reset_match(session_factory, other)


# ---------------------------------------------------------------------------
# Erros: estrutural = FAILED seguro; transitório = retry com backoff
# ---------------------------------------------------------------------------


def _corrupt_purchase(match_id: str) -> TimelineEvent:
    payload = {"participantId": 1, "itemId": "não-é-inteiro"}
    return TimelineEvent(match_id, 90, 700_000, "ITEM_PURCHASED", payload)


@pytest.mark.parametrize("_demo_match", ["DEMO-0002"], indirect=True)
async def test_structural_error_fails_permanently_with_safe_code_then_api_retry_recovers(
    app_client, session_factory, tmp_path, _demo_match
) -> None:
    await import_demo_match(session_factory, tmp_path, "DEMO-0002")
    async with unit_of_work(session_factory) as session:
        await SqlAlchemyTimelineRepository(session).add_event(_corrupt_purchase("DEMO-0002"))
    raw_store = DurableRawMatchStore(tmp_path / "raw")

    assert await process_next_preparation(session_factory, raw_store, _demo_settings()) is True
    [failed] = await _jobs(session_factory, "DEMO-0002")
    assert failed.state == "FAILED" and failed.attempts == 1  # permanente: nada de RETRY_WAIT
    assert failed.error_code and failed.error_code.replace("_", "").isalnum()
    assert "não-é-inteiro" not in failed.error_code
    body = (await app_client.get("/api/v1/matches/DEMO-0002")).json()
    assert body["preparationStatus"] == "FAILED"
    assert body["preparation"]["errorCode"] == failed.error_code
    assert "não-é-inteiro" not in str(body)
    # falha final não se repete sozinha
    assert await process_next_preparation(session_factory, raw_store, _demo_settings()) is False

    # nova tentativa pela API, ainda corrompida: falha de novo, sem inventar sucesso
    assert (await app_client.post("/api/v1/matches/DEMO-0002/preparation")).status_code == 202
    assert await process_next_preparation(session_factory, raw_store, _demo_settings()) is True
    assert [j.state for j in await _jobs(session_factory, "DEMO-0002")] == ["FAILED", "FAILED"]

    # dado corrigido: a nova tentativa pela API chega a READY
    async with unit_of_work(session_factory) as session:
        await session.execute(
            TimelineEventModel.__table__.delete().where(
                TimelineEventModel.match_id == "DEMO-0002", TimelineEventModel.source_index == 90
            )
        )
    assert (await app_client.post("/api/v1/matches/DEMO-0002/preparation")).status_code == 202
    assert await process_next_preparation(session_factory, raw_store, _demo_settings()) is True
    jobs = await _jobs(session_factory, "DEMO-0002")
    assert [j.state for j in jobs] == ["FAILED", "FAILED", "SUCCEEDED"]
    assert (await app_client.get("/api/v1/matches/DEMO-0002")).json()[
        "preparationStatus"
    ] == "READY"


async def test_transient_catalog_error_schedules_retry_then_succeeds(
    session_factory, tmp_path, real_match
) -> None:
    patch = unique_patch()
    settings = write_prepare_configs(tmp_path, scope_patch=patch, mapped_patches=(patch,))
    raw_store = DurableRawMatchStore(tmp_path / "raw")
    await import_real_match(session_factory, raw_store, real_match, patch, ((120_000, 3006),))

    down = stub_catalog_factory(
        error=SourceError(code="DATA_DRAGON_UNAVAILABLE", detail="503 upstream")
    )
    assert await process_next_preparation(session_factory, raw_store, settings, down) is True
    [waiting] = await _jobs(session_factory, real_match)
    assert (waiting.state, waiting.error_code, waiting.attempts) == (
        "RETRY_WAIT",
        "DATA_DRAGON_UNAVAILABLE",
        1,
    )
    assert waiting.retry_at is not None and waiting.retry_at > datetime.now(UTC)
    async with _client(settings) as client:
        body = (await client.get(f"/api/v1/matches/{real_match}")).json()
    assert body["preparation"]["status"] == "PENDING"  # ainda vai tentar
    assert body["preparation"]["errorCode"] == "DATA_DRAGON_UNAVAILABLE"
    assert body["preparation"]["retryAt"] is not None

    # backoff: antes de retry_at nada é reservado
    assert await process_next_preparation(session_factory, raw_store, settings, down) is False
    await _release_retry(session_factory, waiting.id)
    up = stub_catalog_factory(StubCatalogSource())
    assert await process_next_preparation(session_factory, raw_store, settings, up) is True
    [done] = await _jobs(session_factory, real_match)
    assert (done.state, done.attempts, done.error_code) == ("SUCCEEDED", 2, None)


async def test_rate_limited_catalog_retries_after_the_advertised_delay(
    session_factory, tmp_path, real_match
) -> None:
    patch = unique_patch()
    settings = write_prepare_configs(tmp_path, scope_patch=patch, mapped_patches=(patch,))
    raw_store = DurableRawMatchStore(tmp_path / "raw")
    await import_real_match(session_factory, raw_store, real_match, patch, ((120_000, 3006),))

    limited = stub_catalog_factory(
        error=SourceRateLimitedError(
            code="DATA_DRAGON_RATE_LIMITED", detail="429", retry_after_seconds=600
        )
    )
    assert await process_next_preparation(session_factory, raw_store, settings, limited) is True
    [waiting] = await _jobs(session_factory, real_match)
    assert waiting.state == "RETRY_WAIT" and waiting.error_code == "DATA_DRAGON_RATE_LIMITED"
    assert waiting.retry_at is not None
    assert waiting.retry_at >= datetime.now(UTC) + timedelta(seconds=500)


@pytest.mark.parametrize("_demo_match", ["DEMO-0002"], indirect=True)
async def test_unexpected_error_retries_with_generic_code_and_never_persists_the_message(
    session_factory, tmp_path, monkeypatch, _demo_match
) -> None:
    await import_demo_match(session_factory, tmp_path, "DEMO-0002")
    raw_store = DurableRawMatchStore(tmp_path / "raw")

    async def boom(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("token=segredo-que-nao-pode-vazar")

    monkeypatch.setattr(preparation, "reconstruct_match", boom)
    assert await process_next_preparation(session_factory, raw_store, _demo_settings()) is True
    [job] = await _jobs(session_factory, "DEMO-0002")
    assert (job.state, job.error_code) == ("RETRY_WAIT", "PREPARATION_UNEXPECTED_ERROR")
    assert "segredo" not in repr(job.__dict__)


@pytest.mark.parametrize("_demo_match", ["DEMO-0002"], indirect=True)
async def test_retry_limit_ends_in_failed_for_preparation_too(
    session_factory, tmp_path, _demo_match
) -> None:
    await import_demo_match(session_factory, tmp_path, "DEMO-0002")
    [job] = await _jobs(session_factory, "DEMO-0002")
    async with unit_of_work(session_factory) as session:
        await session.execute(
            update(ImportJobModel)
            .where(ImportJobModel.id == job.id)
            .values(state="RETRY_WAIT", attempts=MAX_TRANSIENT_ATTEMPTS, retry_at=datetime.now(UTC))
        )
    raw_store = DurableRawMatchStore(tmp_path / "raw")
    assert await process_next_preparation(session_factory, raw_store, _demo_settings()) is False
    [ended] = await _jobs(session_factory, "DEMO-0002")
    assert (ended.state, ended.error_code) == ("FAILED", "RETRY_LIMIT_EXCEEDED")


# ---------------------------------------------------------------------------
# No máximo uma preparação ativa por partida; import e preparação não se misturam
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("_demo_match", ["DEMO-0002"], indirect=True)
async def test_at_most_one_active_preparation_per_match_under_concurrent_requests(
    session_factory, tmp_path, _demo_match
) -> None:
    await import_demo_match(session_factory, tmp_path, "DEMO-0002")
    results: list[ImportJob] = await asyncio.gather(
        *(_enqueue(session_factory, "DEMO-0002") for _ in range(8))
    )
    assert len({job.id for job in results}) == 1  # todos recebem a mesma preparação ativa
    assert len(await _jobs(session_factory, "DEMO-0002")) == 1


async def _enqueue(session_factory, match_id: str) -> ImportJob:
    async with unit_of_work(session_factory) as session:
        repo = SqlAlchemyIngestionRepository(session)
        mode = await repo.get_match_import_mode(match_id)
        assert mode is not None
        return await repo.enqueue_preparation(match_id, mode)


@pytest.mark.parametrize("_demo_match", ["DEMO-0002"], indirect=True)
async def test_preparation_jobs_are_invisible_to_import_endpoints_and_the_import_worker(
    app_client, session_factory, tmp_path, _demo_match
) -> None:
    await import_demo_match(session_factory, tmp_path, "DEMO-0002")
    [job] = await _jobs(session_factory, "DEMO-0002")
    assert job.kind == JobKind.PREPARE.value and job.state == ImportJobState.QUEUED.value
    response = await app_client.get(f"/api/v1/imports/{job.id}")
    assert response.status_code == 404  # é preparação, não importação
    async with unit_of_work(session_factory) as session:
        reserved = await SqlAlchemyIngestionRepository(session).reserve_next_job(
            datetime.now(UTC) + timedelta(days=1)  # kind IMPORT (padrão)
        )
    assert reserved is None or reserved.kind is JobKind.IMPORT
    if reserved is not None:  # devolve (não deveria existir no isolamento do teste)
        async with unit_of_work(session_factory) as session:
            await SqlAlchemyIngestionRepository(session).mark_import_failed(
                reserved.id, reserved.lease_generation, "TEST_CLEANUP"
            )


def _registry_yaml(supported: dict[str, list[str]]) -> str:
    """Registro do teste: tudo PENDING, exceto as regras dadas (nos patches dados)."""
    rules: dict[str, Any] = {}
    for rule_id in KNOWN_RULE_IDS:
        entry: dict[str, Any] = {
            "ruleVersion": 1,
            "description": f"regra {rule_id} (teste)",
            "status": "PENDING",
            "patchesValidated": [],
            "contraRate": None,
            "counterExamples": None,
            "evidence": None,
            "activatedInVersion": None,
        }
        if rule_id in supported:
            entry.update(
                status="EMPIRICALLY_SUPPORTED",
                patchesValidated=supported[rule_id],
                contraRate=0.0,
                counterExamples=0,
                evidence="evidencia-sintetica-de-teste",
                activatedInVersion=RECONSTRUCTION_ALGORITHM_VERSION,
            )
        rules[rule_id] = entry
    return yaml.safe_dump(
        {
            "schemaVersion": "v2",
            "inheritance": {
                "mode": "LATEST_VALIDATED_EARLIER",
                "activatedInVersion": RECONSTRUCTION_ALGORITHM_VERSION,
            },
            "systemItemIds": [1202],
            "rules": rules,
        }
    )


@pytest.mark.parametrize("_demo_match", ["DEMO-0002"], indirect=True)
async def test_preparation_loads_the_empirical_rules_registry_from_the_configured_path(
    session_factory, tmp_path, _demo_match
) -> None:
    """O worker lê o registro do caminho configurado (Settings.empirical_rules_path) e grava a
    proveniência das regras ligadas no patch da partida; sem regras ligadas, a lista fica vazia."""
    registry = tmp_path / "empirical-rules.yaml"
    registry.write_text(_registry_yaml({"R5": ["DEMO-1"]}))
    settings = Settings(
        subject_hash_secret=FAKE_SUBJECT_HASH_SECRET, empirical_rules_path=str(registry)
    )
    await import_demo_match(session_factory, tmp_path, "DEMO-0002")

    raw_store = DurableRawMatchStore(tmp_path / "raw")
    assert await process_next_preparation(session_factory, raw_store, settings) is True

    async with unit_of_work(session_factory) as session:
        states = await SqlAlchemyTimelineRepository(session).list_decision_states("DEMO-0002", 1)
    assert states and all(
        [rule["ruleId"] for rule in state.quality["empiricalRules"]] == ["R5"] for state in states
    )


@pytest.mark.parametrize("_demo_match", ["DEMO-0002"], indirect=True)
async def test_preparation_with_a_missing_registry_file_runs_with_every_rule_disabled(
    session_factory, tmp_path, _demo_match
) -> None:
    settings = Settings(
        subject_hash_secret=FAKE_SUBJECT_HASH_SECRET,
        empirical_rules_path=str(tmp_path / "nao-existe.yaml"),
    )
    await import_demo_match(session_factory, tmp_path, "DEMO-0002")

    raw_store = DurableRawMatchStore(tmp_path / "raw")
    assert await process_next_preparation(session_factory, raw_store, settings) is True

    async with unit_of_work(session_factory) as session:
        states = await SqlAlchemyTimelineRepository(session).list_decision_states("DEMO-0002", 1)
    assert states and all(state.quality["empiricalRules"] == [] for state in states)


# ---------------------------------------------------------------------------
# Revisão sob demanda e no ocioso (importação da sincronização de histórico)
# ---------------------------------------------------------------------------


async def _hold_import_queue_empty(session_factory) -> None:
    """Importações abertas de outros testes contariam como fila ocupada."""
    async with unit_of_work(session_factory) as session:
        await session.execute(
            delete(ImportJobModel).where(
                ImportJobModel.kind == "IMPORT", ImportJobModel.state.in_(["QUEUED", "RUNNING"])
            )
        )


async def test_lazy_import_stays_unprepared_until_the_review_is_requested(
    app_client, session_factory, tmp_path, real_match
) -> None:
    raw_store = DurableRawMatchStore(tmp_path / "raw")
    await import_real_match(
        session_factory, raw_store, real_match, unique_patch(), prepare_after=False
    )

    assert await _jobs(session_factory, real_match) == []  # nada agendado pela importação
    detail = (await app_client.get(f"/api/v1/matches/{real_match}")).json()
    assert detail["preparation"]["status"] == "NOT_REQUESTED"

    requested = await app_client.post(
        f"/api/v1/matches/{real_match}/preparation", headers={"X-RiftLab-Request": "1"}
    )
    assert requested.status_code in (200, 202)
    [job] = await _jobs(session_factory, real_match)
    assert job.state == "QUEUED"


async def test_idle_preparation_waits_for_the_queues_and_takes_the_newest_match(
    session_factory, tmp_path, real_match
) -> None:
    raw_store = DurableRawMatchStore(tmp_path / "raw")
    await import_real_match(
        session_factory, raw_store, real_match, unique_patch(), prepare_after=False
    )
    async with unit_of_work(session_factory) as session:  # a mais recente de todas
        await session.execute(
            update(MatchModel)
            .where(MatchModel.match_id == real_match)
            .values(start_at=datetime.now(UTC) + timedelta(days=3650))
        )
    await _hold_import_queue_empty(session_factory)

    # Importação na fila: a revisão automática não disputa o worker com ela.
    busy = uuid.uuid4()
    async with unit_of_work(session_factory) as session:
        await SqlAlchemyIngestionRepository(session).create_import_job(
            ImportJob(
                id=busy,
                requested_match_id=f"BR1_{uuid.uuid4().int % 10**10}",
                mode=ImportMode.RIOT,
                state=ImportJobState.QUEUED,
            )
        )
    try:
        assert await enqueue_idle_preparation(session_factory) is False
        assert await _jobs(session_factory, real_match) == []
    finally:
        async with unit_of_work(session_factory) as session:
            await session.execute(delete(ImportJobModel).where(ImportJobModel.id == busy))

    # Filas vazias: agenda uma, a partida mais recente sem revisão.
    assert await enqueue_idle_preparation(session_factory) is True
    [job] = await _jobs(session_factory, real_match)
    assert (job.state, job.mode) == ("QUEUED", "RIOT")
    # Com essa preparação na fila, não agenda outra (um pedido da tela passa na frente).
    assert await enqueue_idle_preparation(session_factory) is False
