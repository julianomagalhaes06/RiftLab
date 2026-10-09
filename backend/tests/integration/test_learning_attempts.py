"""CC-304: study attempts through the API (choice before reveal), demo pipeline."""

from __future__ import annotations

import uuid

import pytest
from test_analyses import _decisions


async def _attempt(app_client, decision_id: str, key: str | None = None):
    return await app_client.post(
        "/api/v1/learning-attempts",
        json={"decisionId": decision_id, "objective": {"priority": "BALANCED"}},
        headers={"Idempotency-Key": key or f"attempt-{uuid.uuid4()}"},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("_demo_match", ["DEMO-0008"], indirect=True)
async def test_choice_before_reveal_then_comparison(
    app_client, session_factory, tmp_path, _demo_match
) -> None:
    decision = (await _decisions(app_client, session_factory, tmp_path, _demo_match))[-1]
    key = f"attempt-{uuid.uuid4()}"
    created = await _attempt(app_client, decision["decisionId"], key)
    again = await _attempt(app_client, decision["decisionId"], key)
    assert created.status_code == 201 and again.status_code == 200
    state = created.json()
    assert state["status"] == "OPEN" and state["options"] == [
        {"itemId": 100010, "name": "Item Composto Sintético"}
    ]
    assert all(set(option) == {"itemId", "name"} for option in state["options"])
    assert (
        state["historicalItemId"] is None
        and state["comparison"] is None
        and state["analysisId"] is None
    )

    attempt = state["attemptId"]
    hinted = (
        await app_client.post(f"/api/v1/learning-attempts/{attempt}/hint", json={"level": 2})
    ).json()
    assert hinted["hintLevel"] == 2 and any("faltam" in h["text"] for h in hinted["hints"])

    wrong = await app_client.post(
        f"/api/v1/learning-attempts/{attempt}/answer", json={"itemId": 1, "skipped": False}
    )
    assert wrong.status_code == 422
    answer = {"itemId": 100010, "skipped": False, "rationale": "aproveita meus componentes"}
    first = await app_client.post(f"/api/v1/learning-attempts/{attempt}/answer", json=answer)
    repeat = await app_client.post(f"/api/v1/learning-attempts/{attempt}/answer", json=answer)
    change = await app_client.post(
        f"/api/v1/learning-attempts/{attempt}/answer", json={"skipped": True}
    )
    assert first.status_code == 200 and first.json()["status"] == "ANSWERED"
    assert repeat.status_code == 200
    assert change.status_code == 409 and change.json()["code"] == "ATTEMPT_LOCKED"

    refused = await app_client.post(
        f"/api/v1/learning-attempts/{attempt}/reveal", json={"explicit": False}
    )
    assert refused.status_code == 422
    revealed = await app_client.post(
        f"/api/v1/learning-attempts/{attempt}/reveal", json={"explicit": True}
    )
    twice = await app_client.post(
        f"/api/v1/learning-attempts/{attempt}/reveal", json={"explicit": True}
    )
    body = revealed.json()
    assert body["status"] == "REVEALED" and body["analysisId"] == twice.json()["analysisId"]
    assert body["comparison"]["candidates"][0]["itemId"] == 100010
    assert body["historicalItemId"] == 100010 and body["selectedItemId"] == 100010


@pytest.mark.asyncio
@pytest.mark.parametrize("_demo_match", ["DEMO-0008"], indirect=True)
async def test_skip_and_reveal_from_open(
    app_client, session_factory, tmp_path, _demo_match
) -> None:
    decision = (await _decisions(app_client, session_factory, tmp_path, _demo_match))[-1]
    attempt = (await _attempt(app_client, decision["decisionId"])).json()["attemptId"]
    revealed = await app_client.post(
        f"/api/v1/learning-attempts/{attempt}/reveal", json={"explicit": True}
    )
    assert revealed.json()["status"] == "REVEALED"
    late = await app_client.post(
        f"/api/v1/learning-attempts/{attempt}/answer", json={"skipped": True}
    )
    assert late.status_code == 409
