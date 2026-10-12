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

from fastapi import APIRouter, Header, Query, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse

from riftlab.analyses.application import create_analysis, get_analysis
from riftlab.errors import ProblemDetail
from riftlab.reviews.application import submit_feedback
from riftlab.reviews.report import build_report, render_html
from riftlab.shared.api.analyses import AnalysisRequest, AnalysisResult
from riftlab.shared.api.enums import ReportFormat
from riftlab.shared.api.feedback import FeedbackRequest, FeedbackResult

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


@router.post(
    "/{analysis_id}/feedback", status_code=201, response_model=FeedbackResult, responses=_ERRORS
)
async def post_feedback(
    analysis_id: uuid.UUID,
    body: FeedbackRequest,
    request: Request,
    response: Response,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=200)],
) -> FeedbackResult:
    """CC-505: julgamento por alternativa (0..3 ou null = abstenção), idempotente."""
    result, created = await submit_feedback(
        request.app.state.session_factory,
        analysis_id,
        body.model_dump(mode="json", by_alias=True),
        idempotency_key,
    )
    if not created:
        response.status_code = 200
    return FeedbackResult.model_validate(result)


# CSP do próprio relatório: nenhuma origem externa, nenhum script.
_REPORT_HEADERS = {
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'",
    "X-Content-Type-Options": "nosniff",
    "Cache-Control": "no-store",
}


@router.get(
    "/{analysis_id}/report",
    responses={
        200: {"content": {"application/json": {}, "text/html": {}}},
        **_ERRORS,
    },
    response_class=Response,
)
async def read_report(
    analysis_id: uuid.UUID,
    request: Request,
    format: ReportFormat = ReportFormat.JSON,  # noqa: A002 — nome do parâmetro no contrato
    attempt_id: Annotated[uuid.UUID | None, Query(alias="attemptId")] = None,
) -> Response:
    """CC-505: exportação rastreável (JSON ou HTML autossuficiente), sem identificadores
    pessoais; com attemptId vinculado inclui resposta inicial, pistas e reflexão."""
    report = await build_report(
        request.app.state.session_factory, analysis_id, attempt_id, _configs(request)
    )
    name = f"riftlab-analise-{str(analysis_id)[:8]}.{format.value}"
    headers = {**_REPORT_HEADERS, "Content-Disposition": f'attachment; filename="{name}"'}
    if format is ReportFormat.HTML:
        return HTMLResponse(render_html(report), headers=headers)
    return JSONResponse(report, headers=headers)
