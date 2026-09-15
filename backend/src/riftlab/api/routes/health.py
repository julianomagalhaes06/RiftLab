from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel
from starlette.responses import JSONResponse

from riftlab import db
from riftlab.errors import DATABASE_UNAVAILABLE, ProblemDetail, problem_response

router = APIRouter(tags=["health"])


class LiveStatus(BaseModel):
    status: str = "UP"


class ReadyStatus(BaseModel):
    status: str = "UP"
    database: str = "UP"


@router.get("/health/live", response_model=LiveStatus)
async def live() -> LiveStatus:
    """Nunca depende do banco; processo vivo responde sempre 200."""
    return LiveStatus()


@router.get(
    "/health/ready",
    response_model=ReadyStatus,
    responses={503: {"model": ProblemDetail, "description": "Banco de dados indisponivel."}},
)
async def ready(request: Request) -> JSONResponse:
    settings = request.app.state.settings
    engine = request.app.state.db_engine
    healthy = await db.ping(engine, settings.database_ready_timeout_seconds)
    if not healthy:
        return problem_response(
            request,
            status_code=503,
            code=DATABASE_UNAVAILABLE,
            title="Banco de dados indisponivel",
            detail="Nao foi possivel confirmar conectividade com o banco dentro do timeout.",
        )
    return JSONResponse(status_code=200, content=ReadyStatus().model_dump())
