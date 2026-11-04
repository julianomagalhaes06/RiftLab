"""POST/GET /api/v1/history/sync (CC-901): estado sem identificadores de conta ou partida."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Request, Response

from riftlab.auth.request_account import not_linked, own_account
from riftlab.history_sync.application import request_sync, sync_status
from riftlab.ingestion.infrastructure.riot_account import RiotAccountConfig
from riftlab.ranked.domain import account_key
from riftlab.shared.api.common import CamelModel
from riftlab.shared.own_account import NOT_LINKED, OwnAccount

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
    account = await own_account(request)
    if not_linked(request, account):
        response.status_code = 200
        return HistorySyncStatus(state="UNAVAILABLE", reason=NOT_LINKED)
    # Conta vinculada (card #13): a execução leva o Riot ID dela até terminar.
    status, created = await request_sync(
        request.app.state.session_factory,
        request.app.state.settings,
        account=_linked(account),
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
    settings = request.app.state.settings
    account = await own_account(request)
    if not_linked(request, account):
        return HistorySyncStatus(state="UNAVAILABLE", reason=NOT_LINKED)
    linked = _linked(account)
    key = account_key(linked, settings.subject_hash_secret) if linked else None
    return HistorySyncStatus.model_validate(
        await sync_status(request.app.state.session_factory, settings, key=key)
    )


def _linked(account: OwnAccount) -> RiotAccountConfig | None:
    """Conta vinculada explícita; a configurada segue o caminho padrão (None)."""
    return account if isinstance(account, RiotAccountConfig) else None
