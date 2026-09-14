from __future__ import annotations

from pydantic import BaseModel
from starlette.requests import Request
from starlette.responses import JSONResponse

PROBLEM_MEDIA_TYPE = "application/problem+json"

DATABASE_UNAVAILABLE = "DATABASE_UNAVAILABLE"
INTERNAL_ERROR = "INTERNAL_ERROR"


class ProblemDetail(BaseModel):
    type: str
    title: str
    status: int
    code: str
    detail: str
    requestId: str


def problem_response(
    request: Request,
    *,
    status_code: int,
    code: str,
    title: str,
    detail: str,
) -> JSONResponse:
    """Monta erro application/problem+json sem DSN, SQL, stack trace ou segredo,
    conforme docs/contracts.md."""
    request_id = getattr(request.state, "request_id", "-")
    problem = ProblemDetail(
        type=f"https://riftlab.local/problems/{code.lower()}",
        title=title,
        status=status_code,
        code=code,
        detail=detail,
        requestId=request_id,
    )
    return JSONResponse(
        status_code=status_code,
        content=problem.model_dump(),
        media_type=PROBLEM_MEDIA_TYPE,
    )
