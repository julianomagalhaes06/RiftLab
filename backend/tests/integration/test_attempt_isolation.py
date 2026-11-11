"""Card #11: cada usuário só vê e altera as próprias tentativas de estudo."""

from __future__ import annotations

import uuid

import pytest
from auth_helpers import sign_in
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete
from test_analyses import _decisions

from riftlab.api.app import create_app
from riftlab.auth.models import UserModel
from riftlab.config import Settings
from riftlab.shared.db import unit_of_work

HEADERS = {"X-RiftLab-Request": "1"}


async def _attempt(client: AsyncClient, decision_id: str, headers: dict[str, str] | None = None):
    return await client.post(
        "/api/v1/learning-attempts",
        json={"decisionId": decision_id, "objective": {"priority": "BALANCED"}},
        headers={"Idempotency-Key": f"attempt-{uuid.uuid4()}", **(headers or {})},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("_demo_match", ["DEMO-0008"], indirect=True)
async def test_attempts_belong_to_their_user(app_client, session_factory, tmp_path, _demo_match):
    decision = (await _decisions(app_client, session_factory, tmp_path, _demo_match))[-1]
    legacy = (await _attempt(app_client, decision["decisionId"])).json()["attemptId"]
    names = [f"iso_{uuid.uuid4().hex[:10]}" for _ in range(2)]
    app = create_app(Settings(auth_required=True))
    try:
        async with app.router.lifespan_context(app):
            transport = ASGITransport(app=app)
            async with (
                AsyncClient(transport=transport, base_url="http://127.0.0.1") as ana,
                AsyncClient(transport=transport, base_url="http://127.0.0.1") as bia,
            ):
                for client, name in zip((ana, bia), names, strict=True):
                    await sign_in(client, session_factory, name)
                created = await _attempt(ana, decision["decisionId"], HEADERS)
                assert created.status_code == 201, created.text
                attempt = created.json()["attemptId"]
                assert (await ana.get(f"/api/v1/learning-attempts/{attempt}")).status_code == 200

                # Outra pessoa não vê nem altera a tentativa: responde como inexistente.
                assert (await bia.get(f"/api/v1/learning-attempts/{attempt}")).status_code == 404
                for path, body in (
                    ("answer", {"skipped": True}),
                    ("hint", {"level": 1}),
                    ("reveal", {"explicit": True}),
                    ("reflection", {"reflection": "não é minha"}),
                ):
                    response = await bia.post(
                        f"/api/v1/learning-attempts/{attempt}/{path}", json=body, headers=HEADERS
                    )
                    assert response.status_code == 404, (path, response.text)
                # A tentativa criada sem sessão também não aparece para quem tem conta.
                assert (await ana.get(f"/api/v1/learning-attempts/{legacy}")).status_code == 404

                # Julgamentos e relatório também são da própria conta.
                revealed = await ana.post(
                    f"/api/v1/learning-attempts/{attempt}/reveal",
                    json={"explicit": True},
                    headers=HEADERS,
                )
                analysis = revealed.json()["comparison"]["analysisId"]
                item = revealed.json()["comparison"]["candidates"][0]["itemId"]
                judged = await ana.post(
                    f"/api/v1/analyses/{analysis}/feedback",
                    json={
                        "itemId": item,
                        "grade": 2,
                        "reason": "faz sentido",
                        "rubricVersion": "rubric_v1",
                        "reviewerAlias": "revisor",
                    },
                    headers={"Idempotency-Key": f"fb-{uuid.uuid4()}", **HEADERS},
                )
                assert judged.status_code == 201, judged.text
                mine = (await ana.get(f"/api/v1/analyses/{analysis}/report")).json()
                theirs = (await bia.get(f"/api/v1/analyses/{analysis}/report")).json()
                assert len(mine["feedback"]) == 1 and theirs["feedback"] == []
                # Relatório com a tentativa de outra pessoa: a tentativa não existe para ela.
                linked = await bia.get(
                    f"/api/v1/analyses/{analysis}/report", params={"attemptId": attempt}
                )
                assert linked.status_code == 404
                # O mesmo apelido em outra conta é outro revisor (sem conflito).
                other = await bia.post(
                    f"/api/v1/analyses/{analysis}/feedback",
                    json={
                        "itemId": item,
                        "grade": 0,
                        "reason": "discordo",
                        "rubricVersion": "rubric_v1",
                        "reviewerAlias": "revisor",
                    },
                    headers={"Idempotency-Key": f"fb-{uuid.uuid4()}", **HEADERS},
                )
                assert other.status_code == 201, other.text

                progress_a = (await ana.get("/api/v1/learning-attempts/progress")).json()
                progress_b = (await bia.get("/api/v1/learning-attempts/progress")).json()
                assert progress_a["study"]["attempts"] == 1
                assert progress_b["study"]["attempts"] == 0
                # A gamificação também é só de quem estudou.
                game_b = (await bia.get("/api/v1/learning-attempts/gamification")).json()
                assert game_b["xp"] == 0 and game_b["studiedDecisions"] == 0
        # Sem sessão (instalação de uma pessoa), a tentativa com dono também é invisível.
        assert (await app_client.get(f"/api/v1/learning-attempts/{attempt}")).status_code == 404
        assert (await app_client.get(f"/api/v1/learning-attempts/{legacy}")).status_code == 200
    finally:
        async with unit_of_work(session_factory) as session:
            await session.execute(delete(UserModel).where(UserModel.email.in_(names)))
