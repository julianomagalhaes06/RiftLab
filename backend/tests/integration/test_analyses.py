"""CC-303: analyses through the API on the demo pipeline (no network)."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from pipeline_helpers import DEMO_1_MAPPING, import_demo_match, sync_demo_catalog
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from riftlab.decisions import features
from riftlab.decisions.application import extract_decisions
from riftlab.ingestion.infrastructure.demo_source import DemoMatchSource
from riftlab.shared.db import unit_of_work
from riftlab.timeline.application import reconstruct_match
from riftlab.timeline.domain import TimelineEvent
from riftlab.timeline.repository import SqlAlchemyTimelineRepository

_NO_SCOPE_FILE = Path("/nonexistent/scope.yaml")


async def _decisions(
    app_client, session_factory, tmp_path, match_id: str, extra: list[TimelineEvent] | None = None
) -> list[dict]:
    await sync_demo_catalog(session_factory)
    await import_demo_match(session_factory, tmp_path, match_id)
    if extra:
        async with unit_of_work(session_factory) as session:
            for event in extra:
                await SqlAlchemyTimelineRepository(session).add_event(event)
    await reconstruct_match(session_factory, DEMO_1_MAPPING, DemoMatchSource(), match_id)
    await extract_decisions(session_factory, match_id, scope_path=_NO_SCOPE_FILE)
    listing = await app_client.get(
        f"/api/v1/matches/{match_id}/decisions", params={"participantId": 1}
    )
    return listing.json()["items"]


@pytest.mark.asyncio
@pytest.mark.parametrize("_demo_match", ["DEMO-0008"], indirect=True)
async def test_demo_analysis_is_available_idempotent_and_immutable(
    app_client, session_factory, tmp_path, _demo_match
) -> None:
    decision = (await _decisions(app_client, session_factory, tmp_path, _demo_match))[-1]
    body = {"decisionId": decision["decisionId"], "objective": {"priority": "BALANCED"}}
    key = {"Idempotency-Key": f"test-{uuid.uuid4()}"}

    first = await app_client.post("/api/v1/analyses", json=body, headers=key)
    again = await app_client.post("/api/v1/analyses", json=body, headers=key)
    other = await app_client.post(
        "/api/v1/analyses", json={**body, "objective": {"priority": "SURVIVAL"}}, headers=key
    )

    assert first.status_code == 201 and again.status_code == 200
    assert first.json() == again.json()
    assert other.status_code == 409 and other.json()["code"] == "IDEMPOTENCY_CONFLICT"
    result = first.json()
    assert (result["status"], result["engine"]) == ("AVAILABLE", "RULES")
    assert [c["itemId"] for c in result["candidates"]] == [100010]  # único item legal do demo
    assert result["support"]["legalCandidates"] == 1
    assert "probabilidades" in result["scoreNote"]
    assert all(e["source"] for c in result["candidates"] for e in c["explanations"])
    read = await app_client.get(f"/api/v1/analyses/{result['analysisId']}")
    assert read.status_code == 200 and read.json() == result
    async with session_factory() as session:
        with pytest.raises(DBAPIError):
            await session.execute(
                text("UPDATE analyses SET status = 'X' WHERE id = :id"),
                {"id": result["analysisId"]},
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("_demo_match", ["DEMO-0008"], indirect=True)
async def test_ineligible_decision_gets_reasons_and_no_items(
    app_client, session_factory, tmp_path, _demo_match
) -> None:
    unowned = TimelineEvent(
        _demo_match, 9_000, 1_000, "ITEM_DESTROYED", {"participantId": 1, "itemId": 100002}
    )
    decisions = await _decisions(app_client, session_factory, tmp_path, _demo_match, [unowned])
    ineligible = [d for d in decisions if not d["eligible"]]
    assert ineligible, decisions
    response = await app_client.post(
        "/api/v1/analyses",
        json={"decisionId": ineligible[-1]["decisionId"], "objective": {"priority": "BALANCED"}},
        headers={"Idempotency-Key": f"test-{uuid.uuid4()}"},
    )
    body = response.json()
    assert response.status_code == 201
    assert body["status"] == "INSUFFICIENT_EVIDENCE" and body["candidates"] == []
    assert body["support"]["reasonCodes"]


@pytest.mark.asyncio
async def test_unknown_decision_and_analysis_are_404(app_client, _migrated_database) -> None:
    missing = await app_client.post(
        "/api/v1/analyses",
        json={"decisionId": str(uuid.uuid4()), "objective": {"priority": "BALANCED"}},
        headers={"Idempotency-Key": f"test-{uuid.uuid4()}"},
    )
    assert missing.status_code == 404
    assert (await app_client.get(f"/api/v1/analyses/{uuid.uuid4()}")).status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("_demo_match", ["DEMO-0008"], indirect=True)
async def test_user_budget_scenario_marks_affordability(
    app_client, session_factory, tmp_path, _demo_match
) -> None:
    decision = (await _decisions(app_client, session_factory, tmp_path, _demo_match))[-1]
    results = {}
    for gold in (100, 5000):
        response = await app_client.post(
            "/api/v1/analyses",
            json={
                "decisionId": decision["decisionId"],
                "objective": {"priority": "BALANCED"},
                "budget": {"gold": gold, "source": "USER_PROVIDED"},
            },
            headers={"Idempotency-Key": f"test-{uuid.uuid4()}"},
        )
        results[gold] = response.json()
    assert results[100]["quality"]["budgetSource"] == "USER_PROVIDED"
    assert results[100]["candidates"][0]["affordability"] == "ABOVE_USER_BUDGET"
    assert results[5000]["candidates"][0]["affordability"] == "WITHIN_USER_BUDGET"


@pytest.mark.asyncio
@pytest.mark.parametrize("_demo_match", ["DEMO-0008"], indirect=True)
async def test_analysis_records_the_active_registry_version_and_keeps_it(
    app_client, session_factory, tmp_path, _demo_match
) -> None:
    from riftlab.ml import registry

    decision = (await _decisions(app_client, session_factory, tmp_path, _demo_match))[-1]
    root = tmp_path / "artifacts"
    root.mkdir()
    (root / "b0.json").write_text("b0")
    (root / "b0-v2.json").write_text("b0 v2")
    dataset = await registry.register_dataset(
        session_factory, root / "b0.json", task="purchase_decision", patch="DEMO-1", description="t"
    )
    versions = []
    for name in ("b0.json", "b0-v2.json"):
        versions.append(
            await registry.register_version(
                session_factory,
                task="purchase_decision",
                scope="DEMO-1",
                kind="BASELINE",
                name=name,
                artifact=root / name,
                artifact_root=root,
                dataset_manifest_id=dataset,
                feature_names=[],
                metrics={},
            )
        )
    body = {"decisionId": decision["decisionId"], "objective": {"priority": "BALANCED"}}
    await registry.promote(session_factory, versions[0])
    first = (
        await app_client.post(
            "/api/v1/analyses", json=body, headers={"Idempotency-Key": f"t-{uuid.uuid4()}"}
        )
    ).json()
    await registry.promote(session_factory, versions[1])
    second = (
        await app_client.post(
            "/api/v1/analyses", json=body, headers={"Idempotency-Key": f"t-{uuid.uuid4()}"}
        )
    ).json()
    assert first["versions"]["registryVersion"] == str(versions[0])
    assert second["versions"]["registryVersion"] == str(versions[1])
    again = (await app_client.get(f"/api/v1/analyses/{first['analysisId']}")).json()
    assert again["versions"]["registryVersion"] == str(
        versions[0]
    )  # análise antiga preserva versão


@pytest.mark.asyncio
@pytest.mark.parametrize("_demo_match", ["DEMO-0008"], indirect=True)
async def test_text_objective_feeds_the_same_ranking_and_keeps_the_parser_version(
    app_client, session_factory, tmp_path, _demo_match
) -> None:
    """CC-504: interpret → confirm → analysis; the parser version is traced, the ranking
    only reads the priority, so the candidates match the form-chosen objective."""
    decision = (await _decisions(app_client, session_factory, tmp_path, _demo_match))[-1]
    interpreted = (
        await app_client.post(
            "/api/v1/objectives/interpret", json={"text": "não quero dano, quero aguentar"}
        )
    ).json()
    assert interpreted["objective"] == {"priority": "SURVIVAL", "parserVersion": "rules_v1"}

    from_text = await app_client.post(
        "/api/v1/analyses",
        json={"decisionId": decision["decisionId"], "objective": interpreted["objective"]},
        headers={"Idempotency-Key": f"text-{uuid.uuid4()}"},
    )
    from_form = await app_client.post(
        "/api/v1/analyses",
        json={"decisionId": decision["decisionId"], "objective": {"priority": "SURVIVAL"}},
        headers={"Idempotency-Key": f"form-{uuid.uuid4()}"},
    )
    assert from_text.status_code == from_form.status_code == 201
    assert from_text.json()["objective"]["parserVersion"] == "rules_v1"
    assert from_form.json()["objective"].get("parserVersion") is None
    assert from_text.json()["candidates"] == from_form.json()["candidates"]


@pytest.mark.asyncio
@pytest.mark.parametrize("_demo_match", ["DEMO-0008"], indirect=True)
async def test_decision_prepared_with_an_older_reconstruction_gets_a_reason_not_500(
    app_client, session_factory, tmp_path, _demo_match, monkeypatch
) -> None:
    """CC-1207: a versão da reconstrução sobe depois da extração. A análise e a prática dessa
    decisão antiga respondem sem alternativas e com o motivo, em vez de erro 500."""
    decision = (await _decisions(app_client, session_factory, tmp_path, _demo_match))[-1]
    assert decision["eligible"]
    monkeypatch.setattr(
        features, "RECONSTRUCTION_ALGORITHM_VERSION", features.RECONSTRUCTION_ALGORITHM_VERSION + 1
    )
    body = {"decisionId": decision["decisionId"], "objective": {"priority": "BALANCED"}}

    analysis = await app_client.post(
        "/api/v1/analyses", json=body, headers={"Idempotency-Key": f"test-{uuid.uuid4()}"}
    )
    assert analysis.status_code == 201
    result = analysis.json()
    assert (result["status"], result["candidates"]) == ("INSUFFICIENT_EVIDENCE", [])
    assert result["support"]["reasonCodes"] == ["STATE_VERSION_INCOMPATIBLE"]

    attempt = await app_client.post(
        "/api/v1/learning-attempts",
        json={**body, "mode": "STUDY"},
        headers={"Idempotency-Key": f"test-{uuid.uuid4()}"},
    )
    assert attempt.status_code == 201
    assert attempt.json()["options"] == []
    assert attempt.json()["reasonCodes"] == ["STATE_VERSION_INCOMPATIBLE"]
