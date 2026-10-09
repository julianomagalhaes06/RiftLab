"""POST /api/v1/analyses e GET /api/v1/analyses/{analysisId} (CC-303).

A análise usa só candidatos do gerador legal e só informação pré-decisão; o
resultado é persistido e imutável. Idempotency-Key obrigatória no POST: mesma
chave e mesmo corpo → 200 com o mesmo resultado; corpo divergente → 409
IDEMPOTENCY_CONFLICT. Pontuações são relativas à decisão, nunca probabilidades.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Header, Request, Response

from riftlab.analyses.application import create_analysis, get_analysis
from riftlab.errors import ProblemDetail
from riftlab.shared.api.analyses import AnalysisRequest, AnalysisResult

router = APIRouter(prefix="/api/v1/analyses", tags=["analyses"])
_CONFIGS = Path(__file__).resolve().parents[5] / "configs"
_ERRORS: dict[int | str, dict[str, object]] = {
    404: {"model": ProblemDetail},
    409: {"model": ProblemDetail},
    422: {"model": ProblemDetail},
}


def _configs(request: Request) -> Path:
    scope = getattr(request.app.state.settings, "scope_path", None)
    configured = Path(scope).parent if scope else None
    return configured if configured is not None and (configured / "ranking").is_dir() else _CONFIGS


@router.post("", status_code=201, response_model=AnalysisResult, responses=_ERRORS)
async def post_analysis(
    body: AnalysisRequest,
    request: Request,
    response: Response,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=200)],
) -> AnalysisResult:
    payload = body.model_dump(mode="json", by_alias=True, exclude_none=True)
    result, created = await create_analysis(
        request.app.state.session_factory, payload, idempotency_key, _configs(request)
    )
    if not created:
        response.status_code = 200
    return AnalysisResult.model_validate(result)


@router.get("/{analysis_id}", response_model=AnalysisResult, responses=_ERRORS)
async def read_analysis(analysis_id: uuid.UUID, request: Request) -> AnalysisResult:
    return AnalysisResult.model_validate(
        await get_analysis(request.app.state.session_factory, analysis_id)
    )
