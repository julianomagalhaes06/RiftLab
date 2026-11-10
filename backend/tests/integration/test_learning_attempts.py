"""CC-304: study attempts through the API (choice before reveal), demo pipeline."""

from __future__ import annotations

import base64
import json
import uuid
from typing import Any

import pytest
from auth_helpers import sign_in, unique_email
from test_analyses import _decisions
from test_decision_routes import _assert_problem


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


def _b64(payload: Any) -> str:
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


@pytest.mark.asyncio
@pytest.mark.parametrize("_demo_match", ["DEMO-0008"], indirect=True)
async def test_list_own_attempts_is_scoped_ordered_and_paginated(
    app_client, session_factory, tmp_path, _demo_match
) -> None:
    await sign_in(app_client, session_factory, unique_email())
    decision = (await _decisions(app_client, session_factory, tmp_path, _demo_match))[-1]

    empty = await app_client.get("/api/v1/learning-attempts")
    assert empty.status_code == 200
    assert empty.json() == {"items": [], "nextCursor": None}

    first = (await _attempt(app_client, decision["decisionId"])).json()["attemptId"]
    second_body = (
        await app_client.post(
            "/api/v1/learning-attempts",
            json={"decisionId": decision["decisionId"], "objective": {"priority": "BALANCED"}},
            headers={"Idempotency-Key": f"attempt-{uuid.uuid4()}"},
        )
    ).json()
    second = second_body["attemptId"]
    await app_client.post(f"/api/v1/learning-attempts/{second}/reveal", json={"explicit": True})
    await app_client.post(
        f"/api/v1/learning-attempts/{second}/reflection", json={"reflection": "Eu mudaria o item."}
    )

    page1 = (await app_client.get("/api/v1/learning-attempts", params={"limit": 1})).json()
    assert [item["attemptId"] for item in page1["items"]] == [second]  # mais recente primeiro
    row = page1["items"][0]
    assert row["matchId"] == _demo_match and row["participantId"] == 1
    assert row["decisionId"] == decision["decisionId"]
    assert row["status"] == "REVEALED" and row["mode"] == "STUDY" and row["skipped"] is False
    assert row["hasReflection"] is True and row["analysisId"] is not None
    assert "reflection" not in row  # texto da reflexão não sai na listagem

    cursor = page1["nextCursor"]
    assert cursor
    page2 = (
        await app_client.get("/api/v1/learning-attempts", params={"cursor": cursor, "limit": 1})
    ).json()
    assert [item["attemptId"] for item in page2["items"]] == [first]
    assert page2["items"][0]["status"] == "OPEN" and page2["items"][0]["hasReflection"] is False
    assert page2["items"][0]["analysisId"] is None
    assert page2["nextCursor"] is None  # fim da lista


@pytest.mark.asyncio
@pytest.mark.parametrize("_demo_match", ["DEMO-0008"], indirect=True)
async def test_list_own_attempts_never_crosses_owner(
    app_client, session_factory, tmp_path, _demo_match
) -> None:
    decision = (await _decisions(app_client, session_factory, tmp_path, _demo_match))[-1]
    await sign_in(app_client, session_factory, unique_email())
    await _attempt(app_client, decision["decisionId"])
    await _attempt(app_client, decision["decisionId"])
    mine = (await app_client.get("/api/v1/learning-attempts", params={"limit": 1})).json()
    valid_cursor = mine["nextCursor"]
    assert valid_cursor  # duas tentativas, limit=1: há próxima página

    # Outra conta não vê nada da primeira, mesmo pedindo um recorte maior.
    await sign_in(app_client, session_factory, unique_email())
    theirs = await app_client.get("/api/v1/learning-attempts", params={"limit": 50})
    assert theirs.json() == {"items": [], "nextCursor": None}

    # Cursor válido para a primeira conta é inválido para a segunda (escopo do dono).
    crossed = await app_client.get("/api/v1/learning-attempts", params={"cursor": valid_cursor})
    _assert_problem(crossed, 422, "INVALID_CURSOR", echoed=valid_cursor)


@pytest.mark.asyncio
@pytest.mark.parametrize("_demo_match", ["DEMO-0008"], indirect=True)
async def test_list_own_attempts_rejects_malformed_cursors(
    app_client, session_factory, tmp_path, _demo_match
) -> None:
    await sign_in(app_client, session_factory, unique_email())
    decision = (await _decisions(app_client, session_factory, tmp_path, _demo_match))[-1]
    await _attempt(app_client, decision["decisionId"])
    await _attempt(app_client, decision["decisionId"])
    page = (await app_client.get("/api/v1/learning-attempts", params={"limit": 1})).json()
    valid = page["nextCursor"]
    assert valid
    padded = valid + "=" * (-len(valid) % 4)
    decoded = json.loads(base64.urlsafe_b64decode(padded))

    bad_cursors = {
        "lixo": "!!!não-é-base64!!!",
        "base64 de não-JSON": _b64("nao e json"),
        "JSON não objeto": _b64([1, 2, 3]),
        "chaves faltando": _b64({"v": 1}),
        "chave extra": _b64({**decoded, "extra": 1}),
        "versão errada": _b64({**decoded, "v": 2}),
        "timestamp texto": _b64({**decoded, "t": "123"}),
        "timestamp bool": _b64({**decoded, "t": True}),
        "timestamp negativo": _b64({**decoded, "t": -1}),
        "ponto não-UUID": _b64({**decoded, "i": "nao-uuid"}),
        "dono vazio": _b64({**decoded, "o": ""}),
        "gigante": "A" * 600,
    }
    for name, cursor in bad_cursors.items():
        response = await app_client.get("/api/v1/learning-attempts", params={"cursor": cursor})
        _assert_problem(response, 422, "INVALID_CURSOR", echoed=cursor[:40])
        assert response.status_code == 422, name

    # cursor legítimo continua funcionando depois das tentativas inválidas
    again = await app_client.get("/api/v1/learning-attempts", params={"cursor": valid})
    assert again.status_code == 200
