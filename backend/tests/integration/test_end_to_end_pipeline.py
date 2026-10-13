"""CC-601: o caminho inteiro pela API num banco limpo, com as falhas críticas no mesmo fluxo.

importação DEMO por HTTP → worker de importação → preparação (catálogo, reconstrução,
extração) → decisões → objetivo por texto → tentativa de estudo → comparação (candidatos
legais + ranking) → reflexão → julgamento → relatório JSON/HTML. Depois, no mesmo estado:
importação e tentativa repetidas não duplicam, interpretador offline cai em NEEDS_INPUT e o
formulário segue, e modelo ativo com arquivo corrompido cai no baseline com motivo gravado
na análise. Sem rede: fixtures DEMO e catálogo sintético.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from pipeline_helpers import FAKE_SUBJECT_HASH_SECRET, purge_open_preparations
from sqlalchemy import func, select

from riftlab.analyses.application import REGISTRY_TASK
from riftlab.api.app import create_app
from riftlab.config import Settings
from riftlab.ingestion.application import process_next_job
from riftlab.ingestion.infrastructure.raw_store import DurableRawMatchStore
from riftlab.ingestion.models import ImportJobModel
from riftlab.ml import registry
from riftlab.preparation.application import process_next_preparation
from riftlab.shared.db import unit_of_work

MATCH = "DEMO-0008"
TEXT = "não quero dano, quero aguentar mais"


@asynccontextmanager
async def _client(settings: Settings) -> AsyncIterator[AsyncClient]:
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client


def _key(prefix: str) -> dict[str, str]:
    return {"Idempotency-Key": f"{prefix}-{uuid.uuid4()}"}


async def _import_and_prepare(app_client: AsyncClient, session_factory, tmp_path: Path) -> str:
    await purge_open_preparations(session_factory)
    created = await app_client.post("/api/v1/imports", json={"matchId": MATCH, "mode": "DEMO"})
    assert created.status_code == 202
    job_id = created.json()["jobId"]

    settings = Settings(subject_hash_secret=FAKE_SUBJECT_HASH_SECRET)
    raw_store = DurableRawMatchStore(tmp_path / "raw")
    assert await process_next_job(session_factory, raw_store, settings) is True
    status = (await app_client.get(f"/api/v1/imports/{job_id}")).json()
    assert status["status"] == "SUCCEEDED" and status["matchId"] == MATCH

    assert await process_next_preparation(session_factory, raw_store, settings) is True
    match = (await app_client.get(f"/api/v1/matches/{MATCH}")).json()
    assert match["preparationStatus"] == "READY"
    return job_id


async def _eligible_decision(app_client: AsyncClient) -> str:
    listing = await app_client.get(
        f"/api/v1/matches/{MATCH}/decisions", params={"participantId": 1}
    )
    assert listing.status_code == 200
    eligible = [d for d in listing.json()["items"] if d["eligible"]]
    assert eligible, "a demonstração precisa de ao menos uma decisão elegível"
    return eligible[-1]["decisionId"]


async def _study(app_client: AsyncClient, decision_id: str, objective: dict) -> dict:
    key = _key("attempt")
    body = {"decisionId": decision_id, "objective": objective, "mode": "STUDY"}
    created = await app_client.post("/api/v1/learning-attempts", json=body, headers=key)
    assert created.status_code == 201
    attempt = created.json()
    again = await app_client.post("/api/v1/learning-attempts", json=body, headers=key)
    assert again.json()["attemptId"] == attempt["attemptId"]  # mesma chave, mesma tentativa
    assert attempt["comparison"] is None and attempt["historicalItemId"] is None  # nada antes

    choice = attempt["options"][0]["itemId"]
    answered = await app_client.post(
        f"/api/v1/learning-attempts/{attempt['attemptId']}/answer",
        json={"itemId": choice, "skipped": False, "rationale": "aguentar o burst"},
    )
    assert answered.status_code == 200
    revealed = await app_client.post(
        f"/api/v1/learning-attempts/{attempt['attemptId']}/reveal", json={"explicit": True}
    )
    assert revealed.status_code == 200
    return revealed.json()


@pytest.mark.asyncio
@pytest.mark.parametrize("_demo_match", [MATCH], indirect=True)
async def test_http_import_to_exported_report_with_text_objective(
    app_client, session_factory, tmp_path, _demo_match
) -> None:
    job_id = await _import_and_prepare(app_client, session_factory, tmp_path)
    decision_id = await _eligible_decision(app_client)

    interpreted = (
        await app_client.post("/api/v1/objectives/interpret", json={"text": TEXT})
    ).json()
    assert interpreted["status"] == "INTERPRETED" and interpreted["provider"] == "RULES"
    state = await _study(app_client, decision_id, interpreted["objective"])

    comparison = state["comparison"]
    assert state["status"] == "REVEALED" and state["analysisId"]
    assert comparison["objective"] == {"priority": "SURVIVAL", "parserVersion": "rules_v1"}
    assert 1 <= len(comparison["candidates"]) <= 3
    assert [c["rank"] for c in comparison["candidates"]] == list(
        range(1, len(comparison["candidates"]) + 1)
    )
    assert all(c["explanations"] for c in comparison["candidates"])

    attempt_id = state["attemptId"]
    reflected = await app_client.post(
        f"/api/v1/learning-attempts/{attempt_id}/reflection", json={"reflection": "mais armadura"}
    )
    assert reflected.status_code == 200
    feedback = await app_client.post(
        f"/api/v1/analyses/{state['analysisId']}/feedback",
        json={
            "itemId": comparison["candidates"][0]["itemId"],
            "grade": 2,
            "reason": "aproveita os componentes",
            "rubricVersion": "rubric_v1",
            "reviewerAlias": "rev-e2e",
        },
        headers=_key("fb"),
    )
    assert feedback.status_code == 201

    report_url = f"/api/v1/analyses/{state['analysisId']}/report"
    report = (
        await app_client.get(report_url, params={"format": "json", "attemptId": attempt_id})
    ).json()
    assert report["analysis"]["objective"]["origin"] == "TEXT_CONFIRMED"
    assert report["analysis"]["objective"]["parserVersion"] == "rules_v1"
    assert "registryFallback" in report["analysis"]["versions"]
    html = await app_client.get(report_url, params={"format": "html", "attemptId": attempt_id})
    assert html.status_code == 200
    assert "default-src 'none'" in html.headers["content-security-policy"]
    assert "<script" not in html.text and MATCH not in html.text and "rev-e2e" not in html.text
    assert "mais armadura" in html.text

    # duplicação: pedir de novo a mesma importação devolve o mesmo job, sem nova partida
    repeated = await app_client.post("/api/v1/imports", json={"matchId": MATCH, "mode": "DEMO"})
    assert repeated.json()["jobId"] == job_id
    async with unit_of_work(session_factory) as session:
        imports = await session.scalar(
            select(func.count())
            .select_from(ImportJobModel)
            .where(ImportJobModel.requested_match_id == MATCH, ImportJobModel.kind == "IMPORT")
        )
    assert imports == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("_demo_match", [MATCH], indirect=True)
async def test_offline_parser_and_corrupted_model_degrade_with_reasons(
    app_client, session_factory, tmp_path, _demo_match
) -> None:
    await _import_and_prepare(app_client, session_factory, tmp_path)
    decision_id = await _eligible_decision(app_client)

    # interpretador local configurado, mas sem serviço escutando: NEEDS_INPUT com motivo
    offline = Settings(
        objective_parser="local_model",
        objective_model_url="http://127.0.0.1:9/interpret",
        objective_model_version="qwen2.5-1.5b-lora-v1",
        objective_parser_timeout_seconds=2.0,
    )
    async with _client(offline) as client:
        answer = (await client.post("/api/v1/objectives/interpret", json={"text": TEXT})).json()
    assert answer["status"] == "NEEDS_INPUT" and answer["objective"] is None
    assert answer["provider"] == "LOCAL_MODEL" and answer["reasonCodes"] == ["PROVIDER_ERROR"]

    # o formulário segue: mesma decisão, objetivo escolhido sem texto
    by_form = await _study(app_client, decision_id, {"priority": "SURVIVAL"})
    assert by_form["comparison"]["candidates"]
    assert by_form["comparison"]["objective"].get("parserVersion") is None

    # modelo ativo com arquivo corrompido: o registro recusa e a análise grava o motivo
    root = tmp_path / "artifacts"
    root.mkdir()
    (root / "m.cbm").write_bytes(b"modelo")
    (root / "b0.json").write_text("b0")
    scope = by_form["comparison"]["versions"]["catalog"].split("/")[0]
    dataset = await registry.register_dataset(
        session_factory, root / "b0.json", task=REGISTRY_TASK, patch=scope, description="e2e"
    )
    common = {
        "task": REGISTRY_TASK,
        "scope": scope,
        "artifact_root": root,
        "dataset_manifest_id": dataset,
        "metrics": {},
    }
    model = await registry.register_version(
        session_factory,
        kind="MODEL",
        name="M-e2e",
        artifact=root / "m.cbm",
        feature_names=["a"],
        **common,
    )
    try:
        await registry.promote(session_factory, model)
        registry._LOADED.clear()
        (root / "m.cbm").write_bytes(b"adulterado")
        body = {"decisionId": decision_id, "objective": {"priority": "SURVIVAL"}}
        analysis = await app_client.post("/api/v1/analyses", json=body, headers=_key("an"))
        assert analysis.status_code == 201
        versions = analysis.json()["versions"]
        assert versions["registryVersion"] is None
        assert versions["registryFallback"] == "ACTIVE_ARTIFACT_HASH_MISMATCH"
        assert analysis.json()["candidates"] == by_form["comparison"]["candidates"]
    finally:
        # devolve o escopo a uma versão íntegra para os demais testes do processo
        baseline = await registry.register_version(
            session_factory,
            kind="BASELINE",
            name="B0-e2e",
            artifact=root / "b0.json",
            feature_names=[],
            **common,
        )
        await registry.promote(session_factory, baseline)
        registry._LOADED.clear()
