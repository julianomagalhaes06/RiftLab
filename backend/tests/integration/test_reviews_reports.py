"""CC-505: feedback, reflection, transfer mode, progress and report export (demo pipeline)."""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_analyses import _decisions

HOSTILE = '<script>alert("x")</script><img src=x onerror=alert(1)> & "aspas"'


async def _revealed_attempt(app_client, decision_id: str, mode: str = "STUDY") -> dict:
    created = await app_client.post(
        "/api/v1/learning-attempts",
        json={"decisionId": decision_id, "objective": {"priority": "SURVIVAL"}, "mode": mode},
        headers={"Idempotency-Key": f"attempt-{uuid.uuid4()}"},
    )
    attempt = created.json()["attemptId"]
    await app_client.post(
        f"/api/v1/learning-attempts/{attempt}/answer",
        json={"itemId": 100010, "skipped": False, "rationale": f"Jogador#BR1 disse {HOSTILE}"},
    )
    return (
        await app_client.post(
            f"/api/v1/learning-attempts/{attempt}/reveal", json={"explicit": True}
        )
    ).json()


def _feedback(grade: int | None = 2, reason: str = "aproveita componentes", alias: str = "rev-a"):
    return {
        "itemId": 100010,
        "grade": grade,
        "reason": reason,
        "rubricVersion": "rubric_v1",
        "reviewerAlias": alias,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("_demo_match", ["DEMO-0008"], indirect=True)
async def test_feedback_is_idempotent_and_never_changes_the_analysis(
    app_client, session_factory, tmp_path, _demo_match
) -> None:
    decision = (await _decisions(app_client, session_factory, tmp_path, _demo_match))[-1]
    state = await _revealed_attempt(app_client, decision["decisionId"])
    analysis_id = state["analysisId"]
    url = f"/api/v1/analyses/{analysis_id}/feedback"
    before = (await app_client.get(f"/api/v1/analyses/{analysis_id}")).json()

    key = {"Idempotency-Key": f"fb-{uuid.uuid4()}"}
    first = await app_client.post(url, json=_feedback(), headers=key)
    same_key = await app_client.post(url, json=_feedback(), headers=key)
    other_body = await app_client.post(url, json=_feedback(grade=0), headers=key)
    same_judgement = await app_client.post(
        url, json=_feedback(), headers={"Idempotency-Key": f"fb-{uuid.uuid4()}"}
    )
    changed_judgement = await app_client.post(
        url, json=_feedback(grade=3), headers={"Idempotency-Key": f"fb-{uuid.uuid4()}"}
    )
    assert first.status_code == 201 and same_key.status_code == 200
    assert first.json() == same_key.json() == same_judgement.json()
    assert "rev-a" not in first.text and len(first.json()["reviewerRef"]) == 12
    assert other_body.status_code == 409 and other_body.json()["code"] == "IDEMPOTENCY_CONFLICT"
    assert changed_judgement.status_code == 409
    assert changed_judgement.json()["code"] == "FEEDBACK_EXISTS"

    abstain = await app_client.post(
        url,
        json=_feedback(grade=None, alias="rev-b"),
        headers={"Idempotency-Key": f"fb-{uuid.uuid4()}"},
    )
    assert abstain.status_code == 201 and abstain.json()["grade"] is None
    after = (await app_client.get(f"/api/v1/analyses/{analysis_id}")).json()
    assert after == before

    async with session_factory() as session:
        count = await session.scalar(
            text("SELECT count(*) FROM analysis_feedback WHERE analysis_id = :id"),
            {"id": analysis_id},
        )
    assert count == 2
    with pytest.raises(DBAPIError, match="feedback is immutable"):
        async with session_factory.begin() as session:
            await session.execute(text("UPDATE analysis_feedback SET grade = 0"))


@pytest.mark.asyncio
@pytest.mark.parametrize("_demo_match", ["DEMO-0008"], indirect=True)
async def test_concurrent_resubmission_stores_one_judgement(
    app_client, session_factory, tmp_path, _demo_match
) -> None:
    decision = (await _decisions(app_client, session_factory, tmp_path, _demo_match))[-1]
    analysis_id = (await _revealed_attempt(app_client, decision["decisionId"]))["analysisId"]
    url = f"/api/v1/analyses/{analysis_id}/feedback"
    responses = await asyncio.gather(
        *(
            app_client.post(
                url,
                json=_feedback(alias="rev-c"),
                headers={"Idempotency-Key": f"fb-{i}-{uuid.uuid4()}"},
            )
            for i in range(6)
        )
    )
    assert sorted(r.status_code for r in responses).count(201) == 1
    assert {r.json()["feedbackId"] for r in responses} == {responses[0].json()["feedbackId"]}


@pytest.mark.asyncio
@pytest.mark.parametrize("_demo_match", ["DEMO-0008"], indirect=True)
async def test_invalid_feedback_is_rejected(
    app_client, session_factory, tmp_path, _demo_match
) -> None:
    decision = (await _decisions(app_client, session_factory, tmp_path, _demo_match))[-1]
    analysis_id = (await _revealed_attempt(app_client, decision["decisionId"]))["analysisId"]
    url = f"/api/v1/analyses/{analysis_id}/feedback"
    key = {"Idempotency-Key": f"fb-{uuid.uuid4()}"}
    assert (await app_client.post(url, json=_feedback(grade=4), headers=key)).status_code == 422
    assert (await app_client.post(url, json=_feedback(grade=-1), headers=key)).status_code == 422
    outside = await app_client.post(url, json={**_feedback(), "itemId": 1}, headers=key)
    assert outside.status_code == 422
    missing = await app_client.post(
        f"/api/v1/analyses/{uuid.uuid4()}/feedback", json=_feedback(), headers=key
    )
    assert missing.status_code == 404
    assert (await app_client.post(url, json=_feedback())).status_code == 422  # sem chave


@pytest.mark.asyncio
@pytest.mark.parametrize("_demo_match", ["DEMO-0008"], indirect=True)
async def test_reflection_once_after_reveal_and_transfer_has_no_hints(
    app_client, session_factory, tmp_path, _demo_match
) -> None:
    decision = (await _decisions(app_client, session_factory, tmp_path, _demo_match))[-1]
    opened = (
        await app_client.post(
            "/api/v1/learning-attempts",
            json={"decisionId": decision["decisionId"], "objective": {"priority": "DAMAGE"}},
            headers={"Idempotency-Key": f"attempt-{uuid.uuid4()}"},
        )
    ).json()
    early = await app_client.post(
        f"/api/v1/learning-attempts/{opened['attemptId']}/reflection", json={"reflection": "x"}
    )
    assert early.status_code == 409 and early.json()["code"] == "ATTEMPT_NOT_REVEALED"

    state = await _revealed_attempt(app_client, decision["decisionId"])
    url = f"/api/v1/learning-attempts/{state['attemptId']}/reflection"
    body = {"reflection": "Com mais magos eu priorizaria resistência mágica."}
    first = await app_client.post(url, json=body)
    again = await app_client.post(url, json=body)
    other = await app_client.post(url, json={"reflection": "mudei de ideia"})
    assert first.status_code == again.status_code == 200
    assert first.json()["reflection"] == body["reflection"]
    assert other.status_code == 409 and other.json()["code"] == "ATTEMPT_LOCKED"

    transfer = (
        await app_client.post(
            "/api/v1/learning-attempts",
            json={
                "decisionId": decision["decisionId"],
                "objective": {"priority": "BALANCED"},
                "mode": "TRANSFER",
            },
            headers={"Idempotency-Key": f"attempt-{uuid.uuid4()}"},
        )
    ).json()
    assert transfer["mode"] == "TRANSFER"
    hint = await app_client.post(
        f"/api/v1/learning-attempts/{transfer['attemptId']}/hint", json={"level": 1}
    )
    assert hint.status_code == 409 and hint.json()["code"] == "HINTS_DISABLED"

    progress = (await app_client.get("/api/v1/learning-attempts/progress")).json()
    assert progress["transfer"]["attempts"] >= 1 and progress["study"]["reflections"] >= 1
    assert set(progress["study"]) == {
        "attempts",
        "answered",
        "skipped",
        "revealed",
        "usedHints",
        "reflections",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("_demo_match", ["DEMO-0008"], indirect=True)
async def test_report_json_and_html_are_traceable_escaped_and_without_personal_ids(
    app_client, session_factory, tmp_path, _demo_match
) -> None:
    decision = (await _decisions(app_client, session_factory, tmp_path, _demo_match))[-1]
    state = await _revealed_attempt(app_client, decision["decisionId"])
    analysis_id, attempt_id = state["analysisId"], state["attemptId"]
    await app_client.post(
        f"/api/v1/learning-attempts/{attempt_id}/reflection",
        json={"reflection": f"{HOSTILE} chave RGAPI-0000-1111"},
    )
    await app_client.post(
        f"/api/v1/analyses/{analysis_id}/feedback",
        json=_feedback(reason=HOSTILE),
        headers={"Idempotency-Key": f"fb-{uuid.uuid4()}"},
    )
    base = f"/api/v1/analyses/{analysis_id}/report"

    as_json = await app_client.get(base, params={"attemptId": attempt_id})
    assert as_json.status_code == 200
    assert "attachment" in as_json.headers["content-disposition"]
    report = as_json.json()
    assert report["analysis"]["engine"] == "RULES"
    assert report["analysis"]["scenario"]["manual"] is False
    assert report["learning"]["initialAnswer"] == "Item Composto Sintético"
    assert report["learning"]["historicalPurchase"] == "Item Composto Sintético"
    assert "Jogador#BR1" not in as_json.text and "RGAPI-" not in as_json.text
    for forbidden in ("DEMO-0008", "matchId", "participantId", "puuid", "rev-a"):
        assert forbidden not in as_json.text

    as_html = await app_client.get(base, params={"attemptId": attempt_id, "format": "html"})
    page = as_html.text
    assert as_html.headers["content-type"].startswith("text/html")
    assert "default-src 'none'" in as_html.headers["content-security-policy"]
    assert "<script" not in page and "onerror=alert" not in page.replace("onerror=alert(1)&gt;", "")
    assert "&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;" in page
    assert "Resposta inicial" in page and "Pistas consultadas" in page and "Reflexão" in page
    assert (
        "http://" not in page and "https://" not in page and "src=" not in page.replace("src=x", "")
    )

    scenario = await app_client.post(
        "/api/v1/analyses",
        json={
            "decisionId": decision["decisionId"],
            "objective": {"priority": "SURVIVAL"},
            "budget": {"gold": 1234, "source": "USER_PROVIDED"},
        },
        headers={"Idempotency-Key": f"sc-{uuid.uuid4()}"},
    )
    manual = (
        await app_client.get(f"/api/v1/analyses/{scenario.json()['analysisId']}/report")
    ).json()
    assert manual["analysis"]["scenario"] == {
        "manual": True,
        "userGold": 1234,
        "label": "Cenário manual com ouro informado (não é o que aconteceu)",
    }
    assert manual["learning"] is None

    foreign = await app_client.get(
        f"/api/v1/analyses/{scenario.json()['analysisId']}/report", params={"attemptId": attempt_id}
    )
    assert foreign.status_code == 422
