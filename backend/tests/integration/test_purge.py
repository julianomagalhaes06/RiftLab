"""CC-603: expurgo de partida contra PostgreSQL real (DEMO, sem rede)."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from pipeline_helpers import FAKE_SUBJECT_HASH_SECRET, purge_open_preparations
from sqlalchemy import func, select, update
from test_analyses import _decisions

from riftlab.analyses.models import AnalysisModel
from riftlab.api.app import create_app
from riftlab.config import Settings
from riftlab.decisions.models import DecisionPointModel
from riftlab.ingestion import purge as purge_module
from riftlab.ingestion.infrastructure.raw_store import DurableRawMatchStore
from riftlab.ingestion.models import ImportJobModel, MatchModel, PurgeTombstoneModel
from riftlab.ingestion.purge import match_ref, process_next_purge, retention
from riftlab.learning.models import LearningAttemptModel
from riftlab.ml import registry
from riftlab.ml.models import (
    DatasetManifestMatchModel,
    DatasetManifestModel,
    ModelEventModel,
    ModelVersionModel,
)
from riftlab.ranked.models import RankedObservationModel
from riftlab.reviews.models import AnalysisFeedbackModel
from riftlab.timeline.models import TimelineEventModel

MATCH = "DEMO-0008"


def _settings() -> Settings:
    """Mesma configuração da API do teste: API e worker precisam do mesmo segredo para a
    lápide ser reconhecida (no compose, ambos leem o mesmo ambiente)."""
    return Settings()


def _key(prefix: str) -> dict[str, str]:
    return {"Idempotency-Key": f"{prefix}-{uuid.uuid4()}"}


async def _derivatives(app_client, session_factory, tmp_path: Path) -> str:
    """Partida com decisão, tentativa revelada, reflexão e julgamento."""
    decision = (await _decisions(app_client, session_factory, tmp_path, MATCH))[-1]
    created = await app_client.post(
        "/api/v1/learning-attempts",
        json={"decisionId": decision["decisionId"], "objective": {"priority": "SURVIVAL"}},
        headers=_key("attempt"),
    )
    attempt = created.json()
    await app_client.post(
        f"/api/v1/learning-attempts/{attempt['attemptId']}/answer",
        json={"itemId": attempt["options"][0]["itemId"], "skipped": False},
    )
    state = (
        await app_client.post(
            f"/api/v1/learning-attempts/{attempt['attemptId']}/reveal", json={"explicit": True}
        )
    ).json()
    await app_client.post(
        f"/api/v1/learning-attempts/{attempt['attemptId']}/reflection",
        json={"reflection": "mais armadura"},
    )
    await app_client.post(
        f"/api/v1/analyses/{state['analysisId']}/feedback",
        json={
            "itemId": state["comparison"]["candidates"][0]["itemId"],
            "grade": 2,
            "reason": "ok",
            "rubricVersion": "rubric_v1",
            "reviewerAlias": "rev-purge",
        },
        headers=_key("fb"),
    )
    return state["analysisId"]


async def _active_model(session_factory, tmp_path: Path) -> tuple[str, uuid.UUID, uuid.UUID]:
    """Dataset que lista esta partida e outra, com um modelo ACTIVE treinado nele."""
    root = tmp_path / "artifacts"
    root.mkdir()
    dataset_path = root / "dataset.json"
    dataset_path.write_text(
        json.dumps(
            {"rows": [{"matchId": MATCH}, {"matchId": "BR1_9999999"}], "nonce": str(uuid.uuid4())}
        )
    )
    (root / "m.cbm").write_bytes(b"modelo")
    task = f"purge-{uuid.uuid4().hex[:8]}"
    dataset = await registry.register_dataset(
        session_factory, dataset_path, task=task, patch="DEMO-1", description="dataset do teste"
    )
    model = await registry.register_version(
        session_factory,
        task=task,
        scope="S",
        kind="MODEL",
        name="M-purge",
        artifact=root / "m.cbm",
        artifact_root=root,
        dataset_manifest_id=dataset,
        feature_names=["a"],
        metrics={},
    )
    await registry.promote(session_factory, model)
    return task, dataset, model


async def _count(session_factory, model, *where) -> int:
    async with session_factory() as session:
        return int(await session.scalar(select(func.count()).select_from(model).where(*where)) or 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("_demo_match", [MATCH], indirect=True)
async def test_purge_removes_every_derivative_invalidates_models_and_keeps_no_id(
    app_client, session_factory, tmp_path, _demo_match
) -> None:
    await purge_open_preparations(session_factory)
    analysis_id = await _derivatives(app_client, session_factory, tmp_path)
    task, dataset, model = await _active_model(session_factory, tmp_path)
    raw = DurableRawMatchStore(tmp_path / "raw-store")
    for kind in ("match", "timeline"):
        body = f"{MATCH}-{kind}".encode()
        raw.store(MATCH, kind, body, hashlib.sha256(body).hexdigest())

    preview = (await app_client.get(f"/api/v1/matches/{MATCH}/purge-preview")).json()
    assert preview["counts"]["analyses"] >= 1 and preview["counts"]["feedback"] == 1
    assert preview["counts"]["learningAttempts"] >= 1 and preview["counts"]["timelineEvents"] > 0
    assert [d["description"] for d in preview["datasets"]] == ["dataset do teste"]
    assert {m["name"]: m["status"] for m in preview["models"]} == {"M-purge": "ACTIVE"}
    assert any("não desaprendem" in text for text in preview["limitations"])

    url = f"/api/v1/matches/{MATCH}"
    assert (await app_client.delete(url)).status_code == 422  # sem confirmação
    assert (await app_client.delete(url, params={"confirm": "DEMO-0001"})).status_code == 422
    first = await app_client.delete(url, params={"confirm": MATCH})
    again = await app_client.delete(url, params={"confirm": MATCH})
    assert first.status_code == again.status_code == 202
    job_id = first.json()["jobId"]
    assert again.json()["jobId"] == job_id  # expurgo ativo reaproveitado

    assert await process_next_purge(session_factory, raw, _settings()) is True

    status = (await app_client.get(f"/api/v1/imports/{job_id}")).json()
    assert status == {**status, "status": "SUCCEEDED", "kind": "PURGE", "matchId": None}
    for table, where in (
        (MatchModel, MatchModel.match_id == MATCH),
        (TimelineEventModel, TimelineEventModel.match_id == MATCH),
        (DecisionPointModel, DecisionPointModel.match_id == MATCH),
        (AnalysisModel, AnalysisModel.id == uuid.UUID(analysis_id)),
        (AnalysisFeedbackModel, AnalysisFeedbackModel.analysis_id == uuid.UUID(analysis_id)),
        (LearningAttemptModel, LearningAttemptModel.analysis_id == uuid.UUID(analysis_id)),
        (ImportJobModel, ImportJobModel.requested_match_id == MATCH),
        (DatasetManifestMatchModel, DatasetManifestMatchModel.match_id == MATCH),
    ):
        assert await _count(session_factory, table, where) == 0, table.__tablename__
    assert not list((tmp_path / "raw-store").glob(f"{MATCH}*"))
    assert (
        await _count(
            session_factory,
            DatasetManifestMatchModel,
            DatasetManifestMatchModel.manifest_id == dataset,
        )
        == 1
    )  # a outra partida do dataset continua listada

    async with session_factory() as session:
        tombstone = await session.scalar(
            select(PurgeTombstoneModel).where(
                PurgeTombstoneModel.match_ref == match_ref(MATCH, _settings().subject_hash_secret)
            )
        )
        job = await session.get(ImportJobModel, uuid.UUID(job_id))
        manifest = await session.get(DatasetManifestModel, dataset)
        version = await session.get(ModelVersionModel, model)
        events = (
            await session.scalars(
                select(ModelEventModel.action).where(ModelEventModel.model_version_id == model)
            )
        ).all()
    assert tombstone is not None and tombstone.reason == "USER_REQUEST"
    assert MATCH not in json.dumps(tombstone.removed) and tombstone.removed["rawFiles"] == 4
    assert job is not None and job.requested_match_id.startswith("PURGED-") and job.match_id is None
    assert manifest is not None and manifest.invalidation_reason == "MATCH_PURGED"
    assert version is not None and version.status == "RETIRED" and "INVALIDATE" in events
    active = await registry.resolve_active(session_factory, task, "S")
    assert active.version_id is None and active.fallback_reason == "NO_ACTIVE_VERSION"
    with pytest.raises(registry.RegistryError, match="dataset invalidado"):
        await registry.promote(session_factory, model)

    # depois do sucesso: a partida não existe mais, e repetir devolve o mesmo job pela lápide
    assert (await app_client.get(url)).status_code == 404
    repeated = await app_client.delete(url, params={"confirm": MATCH})
    assert repeated.status_code == 202 and repeated.json()["jobId"] == job_id


@pytest.mark.asyncio
@pytest.mark.parametrize("_demo_match", [MATCH], indirect=True)
async def test_failure_after_raw_removal_retries_and_finishes(
    app_client, session_factory, tmp_path, _demo_match, monkeypatch
) -> None:
    await purge_open_preparations(session_factory)
    await _decisions(app_client, session_factory, tmp_path, MATCH)
    raw = DurableRawMatchStore(tmp_path / "raw-store")
    body = b"bruto"
    raw.store(MATCH, "match", body, hashlib.sha256(body).hexdigest())
    job_id = (
        await app_client.delete(f"/api/v1/matches/{MATCH}", params={"confirm": MATCH})
    ).json()["jobId"]

    original = purge_module._delete_rows
    calls = {"n": 0}

    async def flaky(session, match_id):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("queda simulada depois de apagar os brutos")
        return await original(session, match_id)

    monkeypatch.setattr(purge_module, "_delete_rows", flaky)
    assert await process_next_purge(session_factory, raw, _settings()) is True
    async with session_factory() as session:
        job = await session.get(ImportJobModel, uuid.UUID(job_id))
        assert job is not None and job.state == "RETRY_WAIT" and job.error_code == "PURGE_FAILED"
    assert await _count(session_factory, MatchModel, MatchModel.match_id == MATCH) == 1
    assert not list((tmp_path / "raw-store").glob(f"{MATCH}*"))  # brutos já tinham saído

    async with session_factory.begin() as session:
        await session.execute(
            update(ImportJobModel)
            .where(ImportJobModel.id == uuid.UUID(job_id))
            .values(retry_at=datetime.now(UTC) - timedelta(seconds=1))
        )
    assert await process_next_purge(session_factory, raw, _settings()) is True
    async with session_factory() as session:
        job = await session.get(ImportJobModel, uuid.UUID(job_id))
        assert job is not None and job.state == "SUCCEEDED"
    assert await _count(session_factory, MatchModel, MatchModel.match_id == MATCH) == 0


@pytest.mark.asyncio
async def test_real_reimport_of_a_purged_match_is_refused(session_factory) -> None:
    match_id = f"BR1_{uuid.uuid4().int % 10**10}"
    async with session_factory.begin() as session:
        session.add(
            PurgeTombstoneModel(
                id=uuid.uuid4(),
                match_ref=match_ref(match_id, FAKE_SUBJECT_HASH_SECRET),
                mode="RIOT",
                reason="USER_REQUEST",
                removed={},
            )
        )
    settings = Settings(
        subject_hash_secret=FAKE_SUBJECT_HASH_SECRET, riot_api_key="RGAPI-fake-test-key-not-real"
    )
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            refused = await client.post(
                "/api/v1/imports", json={"matchId": match_id, "mode": "RIOT"}
            )
    assert refused.status_code == 409 and refused.json()["code"] == "MATCH_PURGED"
    assert (
        await _count(session_factory, ImportJobModel, ImportJobModel.requested_match_id == match_id)
        == 0
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("_demo_match", [MATCH], indirect=True)
async def test_retention_lists_by_default_and_executes_only_when_asked(
    app_client, session_factory, tmp_path, _demo_match
) -> None:
    await purge_open_preparations(session_factory)
    await _decisions(app_client, session_factory, tmp_path, MATCH)
    old = datetime.now(UTC) - timedelta(days=200)
    async with session_factory.begin() as session:
        await session.execute(
            update(ImportJobModel)
            .where(ImportJobModel.requested_match_id == MATCH, ImportJobModel.kind == "IMPORT")
            .values(created_at=old)
        )
        account = f"acc-{uuid.uuid4().hex[:8]}"
        for days in (900, 800, 700):  # três observações antigas da mesma conta e temporada
            session.add(
                RankedObservationModel(
                    account_key=account,
                    queue="RANKED_SOLO_5x5",
                    season="2026-1",
                    captured_at=datetime.now(UTC) - timedelta(days=days),
                    state="READY",
                    origin="RIOT_LEAGUE_V4",
                )
            )
    raw = DurableRawMatchStore(tmp_path / "raw-store")
    common = {"match_days": 90, "rank_days": 365, "secret": FAKE_SUBJECT_HASH_SECRET}

    plan = await retention(session_factory, raw, **common)
    assert plan["dryRun"] is True and plan["matches"] >= 1 and plan["rankObservations"] >= 2
    assert await _count(session_factory, MatchModel, MatchModel.match_id == MATCH) == 1
    assert MATCH not in json.dumps(plan)

    done = await retention(session_factory, raw, execute=True, **common)
    assert done["dryRun"] is False and done["purgedMatches"] >= 1
    assert await _count(session_factory, MatchModel, MatchModel.match_id == MATCH) == 0
    kept = await _count(
        session_factory, RankedObservationModel, RankedObservationModel.account_key == account
    )
    assert kept == 1  # a observação mais recente da fila e temporada fica
    async with session_factory() as session:
        reason = await session.scalar(
            select(PurgeTombstoneModel.reason).where(
                PurgeTombstoneModel.match_ref == match_ref(MATCH, FAKE_SUBJECT_HASH_SECRET)
            )
        )
    assert reason in {"RETENTION", "USER_REQUEST"}  # lápide pode vir de teste anterior
