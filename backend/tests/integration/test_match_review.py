"""F1 (CC-1003/CC-1004): revisão da partida pela API, no pipeline de demonstração (sem rede)."""

from __future__ import annotations

import json

import pytest
from test_analyses import _decisions

from riftlab.match_review.win_probability import FEATURE_NAMES


def _use(app_client, **paths: str) -> None:
    app = app_client._transport.app  # noqa: SLF001 — só o teste troca os caminhos
    app.state.settings = app.state.settings.model_copy(update=paths)


@pytest.mark.asyncio
@pytest.mark.parametrize("_demo_match", ["DEMO-0008"], indirect=True)
async def test_review_labels_purchases_against_the_reference(
    app_client, session_factory, tmp_path, _demo_match
) -> None:
    decisions = await _decisions(app_client, session_factory, tmp_path, _demo_match)
    detail = (await app_client.get(f"/api/v1/matches/{_demo_match}")).json()
    champion = next(p for p in detail["participants"] if p["participantId"] == 1)["championId"]

    _use(
        app_client,
        match_review_reference_path=str(tmp_path / "sem-referencia.json"),
        win_probability_model_path=str(tmp_path / "sem-modelo.json"),
    )
    missing = await app_client.get(
        f"/api/v1/matches/{_demo_match}/review", params={"participantId": 1}
    )
    assert missing.status_code == 200
    body = missing.json()
    assert body["referenceVersion"] is None
    assert body["winProbabilityUnavailable"] == "MODEL_UNAVAILABLE"
    assert all(p["label"] is None for p in body["purchases"])

    review_points = [p for p in body["purchases"] if p["status"] == "READY"]
    assert review_points, "a demonstração precisa de ao menos uma compra pronta"
    first = review_points[0]
    reference = {
        "schemaVersion": "match_review_reference_v1",
        "referenceVersion": "teste-v1",
        "patch": "16.19",
        "queueId": 420,
        "source": {"dataDragonVersion": "16.19.1"},
        "purchases": {
            f"{champion}:{body['role']}:{first['ordinal']}": {
                "n": 40,
                "items": [[first["itemId"], 30], [1, 10]],
            }
        },
        "skills": {},
    }
    model = {
        "modelVersion": "teste-wp-v1",
        "featureNames": list(FEATURE_NAMES),
        "coefficients": [0.0] * len(FEATURE_NAMES),
        "gate": {"passed": False},
    }
    (tmp_path / "reference.json").write_text(json.dumps(reference))
    (tmp_path / "model.json").write_text(json.dumps(model))
    _use(
        app_client,
        match_review_reference_path=str(tmp_path / "reference.json"),
        win_probability_model_path=str(tmp_path / "model.json"),
    )

    labeled = (
        await app_client.get(f"/api/v1/matches/{_demo_match}/review", params={"participantId": 1})
    ).json()
    target = next(p for p in labeled["purchases"] if p["decisionId"] == first["decisionId"])
    assert (target["label"], target["share"], target["rank"], target["support"]) == (
        "COMMON",
        0.75,
        1,
        40,
    )
    assert target["referenceTop"][0] == {"itemId": first["itemId"], "share": 0.75}
    assert labeled["referenceVersion"] == "teste-v1"
    assert labeled["referenceInherited"] is True  # patch DEMO ≠ 16.19
    assert labeled["winProbabilityUnavailable"] == "MODEL_GATE_FAILED"
    assert labeled["counts"]["ready"] == len(review_points)
    assert {p["decisionId"] for p in labeled["purchases"]} <= {d["decisionId"] for d in decisions}

    # Selo do Histórico (CC-1005): contagem da conta local (DEMO: participante 1).
    listing = (await app_client.get("/api/v1/matches", params={"limit": 50})).json()["items"]
    summary = next(item for item in listing if item["matchId"] == _demo_match)
    assert summary["reviewPurchases"] == {
        "ready": len(review_points),
        "total": len(body["purchases"]),
    }


@pytest.mark.asyncio
async def test_review_of_a_missing_match_or_participant_is_404(app_client) -> None:
    response = await app_client.get("/api/v1/matches/DEMO-9999/review", params={"participantId": 1})
    assert response.status_code == 404
    invalid = await app_client.get("/api/v1/matches/DEMO-0008/review", params={"participantId": 11})
    assert invalid.status_code == 422
