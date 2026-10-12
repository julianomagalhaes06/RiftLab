"""POST /api/v1/analyses/{id}/feedback, GET .../report (docs/contracts.md, UC04;
rotas no CC-505)."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from pydantic import Field

from riftlab.shared.api.common import CamelModel
from riftlab.shared.api.enums import ReportFormat

GRADE_MIN = 0
GRADE_MAX = 3
# docs/contracts.md nao fixa um limite de tamanho para "reason"; 1000 aqui e
# limite defensivo de engenharia (mesma ordem do rationale de learning-attempt,
# que E contratual — ver learning.py), nao uma cota normativa do contrato.
FEEDBACK_REASON_MAX_LENGTH = 1000


class FeedbackRequest(CamelModel):
    """{itemId,grade,reason,rubricVersion,reviewerAlias} + Idempotency-Key.
    grade 0..3 ou null para abstencao (docs/contracts.md)."""

    item_id: int = Field(ge=0)
    grade: Annotated[int, Field(ge=GRADE_MIN, le=GRADE_MAX)] | None
    reason: str = Field(min_length=1, max_length=FEEDBACK_REASON_MAX_LENGTH)
    rubric_version: str = Field(min_length=1, max_length=40)
    reviewer_alias: str = Field(min_length=1, max_length=80)


class FeedbackResult(CamelModel):
    """Julgamento gravado (imutável). O pseudônimo não volta: só uma referência curta do
    hash, para distinguir revisores sem identificá-los."""

    feedback_id: UUID
    analysis_id: UUID
    item_id: int = Field(ge=0)
    grade: Annotated[int, Field(ge=GRADE_MIN, le=GRADE_MAX)] | None
    reason: str
    rubric_version: str
    reviewer_ref: str = Field(min_length=12, max_length=12)


class ReportQuery(CamelModel):
    """GET /analyses/{id}/report?format=json|html[&attemptId=]."""

    format: ReportFormat = ReportFormat.JSON
    attempt_id: UUID | None = None
