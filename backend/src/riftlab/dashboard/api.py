from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Query, Request

from riftlab.shared.api.common import CamelModel
from riftlab.shared.api.matches import ChampionAsset, MatchParticipant

router = APIRouter(tags=["dashboard"])


class DashboardMatch(CamelModel):
    match_id: str
    ended_at: datetime
    duration_seconds: int
    participant: MatchParticipant


class DashboardChampion(CamelModel):
    champion_id: int
    champion: ChampionAsset | None
    matches: int


class DashboardProgress(CamelModel):
    attempts: int
    revealed: int
    reflections: int
    matches: int


class DashboardQueue(CamelModel):
    queue: int
    total_matches: int


class Dashboard(CamelModel):
    """`queue` é a fila do recorte (420 Solo/Duo, 440 Flex). Sem `queue` no pedido, é a fila
    da partida própria mais recente (`queueSelection` AUTO); `queues` traz o total por fila."""

    origin: Literal["REAL", "DEMO"]
    status: Literal["READY", "EMPTY", "IDENTITY_UNAVAILABLE"]
    window: int
    queue: int
    queue_selection: Literal["AUTO", "EXPLICIT"]
    queues: list[DashboardQueue]
    total_matches: int
    sampled_matches: int
    win_samples: int
    wins: int
    win_rate: float | None
    kda_samples: int
    kda: float | None
    distinct_champions: int
    champions: list[DashboardChampion]
    recent_matches: list[DashboardMatch]
    progress: DashboardProgress


@router.get(
    "/api/v1/dashboard",
    response_model=Dashboard,
    summary="Painel descritivo da conta própria ou demonstração explícita",
    description="Só leitura no banco. Sem rede, coleta ou identificação de terceiros. "
    "DEMO usa o participante sintético 1; progresso é local, por origem e de todas as datas. "
    "`queue` escolhe Solo/Duo ou Flex; sem ele, vale a fila da partida própria mais recente.",
)
async def get_dashboard(
    request: Request,
    window: Annotated[int, Query(ge=1, le=100)] = 20,
    origin: Literal["REAL", "DEMO"] = "REAL",
    queue: Literal["RANKED_SOLO_5x5", "RANKED_FLEX_SR"] | None = None,
) -> Dashboard:
    from riftlab.auth.request_account import own_account
    from riftlab.dashboard.application import QUEUE_IDS, dashboard

    return await dashboard(
        request.app.state.session_factory,
        request.app.state.settings,
        window=window,
        origin=origin,
        queue=QUEUE_IDS[queue] if queue else None,
        account=await own_account(request),
    )
