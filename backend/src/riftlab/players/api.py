"""Perfil de jogador por Riot ID (CC-1101): busca, perfil guardado e sincronização do histórico."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Request, Response
from pydantic import Field

from riftlab.errors import ProblemDetail
from riftlab.history_sync.api import HistorySyncStatus
from riftlab.history_sync.application import request_sync, sync_status
from riftlab.players.application import (
    lookup_player,
    own_player,
    profile_account,
    profile_key,
)
from riftlab.shared.api.common import CamelModel

router = APIRouter(prefix="/api/v1/players", tags=["players"])


class PlayerLookup(CamelModel):
    game_name: str = Field(min_length=1, max_length=32)
    tag_line: str = Field(min_length=1, max_length=8)
    refresh: bool = False


class PlayerSyncRequest(CamelModel):
    """Riot ID do perfil: o banco guarda só o HMAC dele (CC-1103), então o pedido o traz."""

    game_name: str = Field(min_length=1, max_length=32)
    tag_line: str = Field(min_length=1, max_length=8)


class PlayerRank(CamelModel):
    queue: Literal["RANKED_SOLO_5x5", "RANKED_FLEX_SR"]
    state: Literal["READY", "UNRANKED"]
    tier: str | None = None
    division: str | None = None
    league_points: int | None = None
    wins: int | None = None
    losses: int | None = None
    observed_at: datetime


class PlayerProfile(CamelModel):
    """Perfil público de um Riot ID (BR1). Sem PUUID nem chave interna. `gameName`/`tagLine`:
    os canônicos da Riot quando consultada agora; senão os do pedido, normalizados (o banco não
    guarda o Riot ID). `staleReason` explica quando o perfil guardado foi devolvido sem nova
    consulta (ex.: RIOT_KEY_MISSING)."""

    id: uuid.UUID
    game_name: str
    tag_line: str
    profile_icon_id: int | None = None
    summoner_level: int | None = None
    refreshed_at: datetime
    data_dragon_version: str | None = None
    solo: PlayerRank | None = None
    flex: PlayerRank | None = None
    stale_reason: str | None = None


_ERRORS: dict[int | str, dict[str, Any]] = {
    404: {"model": ProblemDetail, "description": "PLAYER_NOT_FOUND ou PLAYER_NOT_ON_PLATFORM."},
    422: {"model": ProblemDetail, "description": "INVALID_RIOT_ID ou VALIDATION_ERROR."},
    503: {
        "model": ProblemDetail,
        "description": (
            "RIOT_KEY_MISSING, RIOT_AUTH_FAILED, RIOT_RATE_LIMITED, UPSTREAM_UNAVAILABLE."
        ),
    },
}


@router.post(
    "/lookup",
    response_model=PlayerProfile,
    summary="Busca um jogador pelo Riot ID (Nome#TAG)",
    description=(
        "Como em sites de estatísticas, qualquer Riot ID do servidor BR pode ser consultado. "
        "Devolve o perfil guardado se tiver menos de 10 min (ou se o servidor estiver sem chave "
        "da Riot); senão consulta a Riot (conta, invocador e elo; 3 requisições) e guarda o "
        "perfil. `refresh` força a consulta."
    ),
    responses=_ERRORS,
)
async def post_player_lookup(body: PlayerLookup, request: Request) -> PlayerProfile:
    return PlayerProfile.model_validate(
        await lookup_player(
            request.app.state.session_factory,
            request.app.state.settings,
            body.game_name,
            body.tag_line,
            refresh=body.refresh,
        )
    )


@router.get(
    "/me",
    response_model=PlayerProfile,
    summary="Perfil da conta configurada",
    description=(
        "Perfil guardado da conta própria (chip do topo), sem nova consulta à Riot; o Riot ID "
        "vem do arquivo da conta. Só na primeira vez, sem perfil guardado, consulta a Riot como "
        "a busca (3 requisições)."
    ),
    responses={
        404: {"model": ProblemDetail, "description": "Conta não configurada."},
        503: _ERRORS[503],
    },
)
async def get_own_player(request: Request) -> PlayerProfile:
    return PlayerProfile.model_validate(
        await own_player(request.app.state.session_factory, request.app.state.settings)
    )


@router.post(
    "/{player_id}/sync",
    response_model=HistorySyncStatus,
    status_code=202,
    summary="Pede a sincronização do histórico do jogador",
    description=(
        "Mesmo fluxo de /api/v1/history/sync (CC-901) para o Riot ID do corpo, que precisa "
        "ser o do perfil: o worker lista as partidas ranqueadas recentes e importa as que "
        "faltam. O Riot ID fica na execução só até ela terminar."
    ),
    responses={
        404: {"model": ProblemDetail, "description": "Jogador não buscado ou outro Riot ID."},
        422: {"model": ProblemDetail, "description": "INVALID_RIOT_ID ou VALIDATION_ERROR."},
    },
)
async def post_player_sync(
    player_id: uuid.UUID, body: PlayerSyncRequest, request: Request, response: Response
) -> HistorySyncStatus:
    factory, settings = request.app.state.session_factory, request.app.state.settings
    account = await profile_account(factory, settings, player_id, body.game_name, body.tag_line)
    status, created = await request_sync(factory, settings, account=account)
    if not created:
        response.status_code = 200
    return HistorySyncStatus.model_validate(status)


@router.get(
    "/{player_id}/sync",
    response_model=HistorySyncStatus,
    summary="Estado da última sincronização do histórico do jogador",
    description="Somente banco; nenhuma chamada à Riot.",
    responses={404: {"model": ProblemDetail, "description": "Jogador não buscado ainda."}},
)
async def get_player_sync(player_id: uuid.UUID, request: Request) -> HistorySyncStatus:
    factory = request.app.state.session_factory
    return HistorySyncStatus.model_validate(
        await sync_status(
            factory, request.app.state.settings, key=await profile_key(factory, player_id)
        )
    )
