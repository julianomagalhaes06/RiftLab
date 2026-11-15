"""GET /api/v1/admin/backstage: Bastidores, só para administradores."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Request

from riftlab.errors import ProblemDetail
from riftlab.shared.api.common import CamelModel
from riftlab.shared.errors import NotFoundError

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


class RiotKeyState(CamelModel):
    """`status`: OK (última resposta conclusiva foi 200), REJECTED (401/403) ou UNKNOWN (sem
    resposta conclusiva nas últimas 24 h)."""

    status: Literal["OK", "REJECTED", "UNKNOWN"]
    checked_at: datetime | None = None


class KindStats(CamelModel):
    kind: str
    calls: int
    average_ms: int


class LastHour(CamelModel):
    calls: int
    succeeded: int
    auth_failures: int
    rate_limited: int
    average_ms: int | None = None
    by_kind: list[KindStats]


class RiotCallRow(CamelModel):
    """Uma tentativa HTTP à Riot. `kind`: account, match-list, match, timeline, league,
    summoner, spectator...; `outcome`: HTTP_200, HTTP_AUTH, HTTP_NOT_FOUND, HTTP_429, HTTP_5XX,
    HTTP_UNEXPECTED ou NETWORK_ERROR."""

    id: int
    at: datetime
    process: Literal["api", "worker", "cli"]
    kind: str
    region: str
    outcome: str
    status_code: int | None = None
    duration_ms: int
    attempt: int


class QueueKind(CamelModel):
    kind: str
    states: dict[str, int]


class WorkerState(CamelModel):
    last_riot_call_at: datetime | None = None
    queue: list[QueueKind]
    succeeded_today: int
    failed_today: int


class JobRow(CamelModel):
    match_id: str
    kind: str
    mode: str
    state: str
    attempts: int
    error_code: str | None = None
    created_at: datetime
    updated_at: datetime
    from_sync: bool


class SyncRow(CamelModel):
    state: str
    requested_at: datetime
    finished_at: datetime | None = None
    listed: int
    new_enqueued: int
    already_known: int
    error_code: str | None = None
    steps: dict[str, int] | None = None


class Counters(CamelModel):
    matches: int
    participants: int
    timeline_events: int
    decisions: int
    study_attempts: int
    players_searched: int
    users: int
    linked_accounts: int


class Backstage(CamelModel):
    generated_at: datetime
    riot_key: RiotKeyState
    last_hour: LastHour
    calls: list[RiotCallRow]
    worker: WorkerState
    jobs: list[JobRow]
    syncs: list[SyncRow]
    counters: Counters


@router.get(
    "/backstage",
    response_model=Backstage,
    summary="Bastidores: chamadas à Riot, fila do worker e acervo",
    description=(
        "Somente banco; nenhuma chamada à Riot. Só para administradores: para qualquer outra "
        "conta (ou sem conta) responde 404, como se a rota não existisse."
    ),
    responses={404: {"model": ProblemDetail, "description": "Conta sem papel de administrador."}},
)
async def get_backstage(request: Request) -> Backstage:
    from riftlab.backstage.application import snapshot

    user = getattr(request.state, "user", None)
    if user is None or not user.is_admin:
        raise NotFoundError("recurso não encontrado")
    return Backstage.model_validate(await snapshot(request.app.state.session_factory))
