"""POST/GET /api/v1/history/sync (CC-901): estado sem identificadores de conta ou partida."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Request, Response

from riftlab.history_sync.application import request_sync, sync_status
from riftlab.shared.api.common import CamelModel

router = APIRouter(prefix="/api/v1/history/sync", tags=["history"])


class HistorySyncStatus(CamelModel):
    """`reason`: RIOT_KEY_MISSING, ACCOUNT_NOT_CONFIGURED, o código do erro da última execução
    (ex.: RIOT_AUTH_FAILED, RIOT_RATE_LIMITED) ou COOLDOWN quando a última foi bem-sucedida e
    o intervalo mínimo ainda vale. `nextAllowedAt` diz quando um novo pedido cria execução."""

    state: Literal["IDLE", "QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "UNAVAILABLE"]
    reason: str | None = None
    requested_at: datetime | None = None
    finished_at: datetime | None = None
    next_allowed_at: datetime | None = None
    listed: int = 0
    new_enqueued: int = 0
    already_known: int = 0
    skipped_purged: int = 0


@router.post(
    "",
    response_model=HistorySyncStatus,
    status_code=202,
    summary="Pede a sincronização do histórico da conta própria",
    description=(
        "Cria uma execução para o worker (202) ou devolve a ativa, o intervalo mínimo ainda "
        "vigente ou a indisponibilidade (200). Nenhuma chamada à Riot dentro da requisição."
    ),
)
async def post_history_sync(request: Request, response: Response) -> HistorySyncStatus:
    status, created = await request_sync(
        request.app.state.session_factory, request.app.state.settings
    )
    if not created:
        response.status_code = 200
    return HistorySyncStatus.model_validate(status)


@router.get(
    "",
    response_model=HistorySyncStatus,
    summary="Estado da última sincronização do histórico",
    description="Somente banco; nenhuma chamada à Riot.",
)
async def get_history_sync(request: Request) -> HistorySyncStatus:
    return HistorySyncStatus.model_validate(
        await sync_status(request.app.state.session_factory, request.app.state.settings)
    )
