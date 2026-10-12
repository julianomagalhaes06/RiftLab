"""CC-505: idempotent reviewer judgements on an immutable analysis.

One judgement per (analysis, item, reviewer pseudonym, rubric version). The same
Idempotency-Key with the same body returns the stored judgement; a different body is 409
IDEMPOTENCY_CONFLICT. A second judgement for the same tuple is accepted only if identical
(200), otherwise 409 FEEDBACK_EXISTS. The pseudonym is stored only as a hash and never
returned. Feedback never changes the analysis and is never used for training.
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from riftlab.analyses.application import request_hash
from riftlab.analyses.models import AnalysisModel
from riftlab.reviews.models import AnalysisFeedbackModel
from riftlab.shared.errors import ConflictError, InvalidRequestError, NotFoundError


def reviewer_hash(alias: str) -> str:
    return hashlib.sha256(f"riftlab-reviewer-v1:{alias.strip().casefold()}".encode()).hexdigest()


def as_payload(row: AnalysisFeedbackModel) -> dict[str, Any]:
    return {
        "feedbackId": str(row.id),
        "analysisId": str(row.analysis_id),
        "itemId": row.item_id,
        "grade": row.grade,
        "reason": row.reason,
        "rubricVersion": row.rubric_version,
        "reviewerRef": row.reviewer_hash[:12],
    }


async def submit_feedback(
    session_factory: async_sessionmaker[AsyncSession],
    analysis_id: uuid.UUID,
    body: dict[str, Any],
    idempotency_key: str,
) -> tuple[dict[str, Any], bool]:
    digest = request_hash({"analysisId": str(analysis_id), **body})
    who = reviewer_hash(body["reviewerAlias"])
    async with session_factory() as session:
        by_key = await session.scalar(
            select(AnalysisFeedbackModel).where(
                AnalysisFeedbackModel.idempotency_key == idempotency_key
            )
        )
        if by_key is not None:
            if by_key.request_sha256 != digest:
                raise ConflictError("IDEMPOTENCY_CONFLICT")
            return as_payload(by_key), False
        analysis = await session.get(AnalysisModel, analysis_id)
        if analysis is None:
            raise NotFoundError(f"análise {analysis_id} não encontrada")
        items = {c["itemId"] for c in analysis.result.get("candidates", [])}
        if body["itemId"] not in items:
            raise InvalidRequestError("item fora das alternativas da análise")
        same = await session.scalar(
            select(AnalysisFeedbackModel).where(
                AnalysisFeedbackModel.analysis_id == analysis_id,
                AnalysisFeedbackModel.item_id == body["itemId"],
                AnalysisFeedbackModel.reviewer_hash == who,
                AnalysisFeedbackModel.rubric_version == body["rubricVersion"],
            )
        )
        if same is not None:
            if (same.grade, same.reason) != (body["grade"], body["reason"]):
                raise ConflictError("FEEDBACK_EXISTS")
            return as_payload(same), False
    row = AnalysisFeedbackModel(
        id=uuid.uuid4(),
        analysis_id=analysis_id,
        item_id=body["itemId"],
        reviewer_hash=who,
        rubric_version=body["rubricVersion"],
        grade=body["grade"],
        reason=body["reason"],
        idempotency_key=idempotency_key,
        request_sha256=digest,
    )
    try:
        async with session_factory.begin() as session:
            session.add(row)
    except IntegrityError:
        # corrida: outra requisição gravou a mesma chave ou o mesmo julgamento
        return await submit_feedback(session_factory, analysis_id, body, idempotency_key)
    return as_payload(row), True


async def list_feedback(session: AsyncSession, analysis_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            select(AnalysisFeedbackModel)
            .where(AnalysisFeedbackModel.analysis_id == analysis_id)
            .order_by(AnalysisFeedbackModel.created_at, AnalysisFeedbackModel.id)
        )
    ).scalars()
    return [as_payload(row) for row in rows]
