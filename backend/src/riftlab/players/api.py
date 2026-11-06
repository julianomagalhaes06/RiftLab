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
    lookup_player_from_match,
    own_player,
    profile_account,
    profile_key,
)
from riftlab.players.live import live_game, live_status
from riftlab.shared.api.common import CamelModel, MatchId
from riftlab.shared.api.matches import ChampionAsset
from riftlab.shared.errors import NotFoundError, PlayerLookupError

router = APIRouter(prefix="/api/v1/players", tags=["players"])


class PlayerLookup(CamelModel):
    game_name: str = Field(min_length=1, max_length=32)
    tag_line: str = Field(min_length=1, max_length=8)
    refresh: bool = False


class PlayerFromMatch(CamelModel):
    """Participante de uma partida importada (o perfil é achado pela conta, não pelo nome)."""

    match_id: MatchId
    participant_id: int = Field(ge=1, le=10)


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


@router.post(
    "/lookup-from-match",
    response_model=PlayerProfile,
    summary="Perfil de um participante de partida importada, pelo Riot ID atual",
    description=(
        "Para o placar: o Riot ID gravado na partida pode estar desatualizado (o jogador trocou "
        "de nome) ou ter passado a outra pessoa. A conta é achada pelo PUUID do bruto da "
        "partida (lido na hora, nunca guardado nem devolvido) e a Account-V1 dá o Riot ID atual; "
        "depois, como a busca: invocador e elo (3 requisições) e o perfil é guardado."
    ),
    responses=_ERRORS,
)
async def post_player_from_match(body: PlayerFromMatch, request: Request) -> PlayerProfile:
    return PlayerProfile.model_validate(
        await lookup_player_from_match(
            request.app.state.session_factory,
            request.app.state.settings,
            body.match_id,
            body.participant_id,
        )
    )


class LivePlayerRank(CamelModel):
    tier: str
    division: str | None = None
    league_points: int
    wins: int
    losses: int


class LiveParticipant(CamelModel):
    """Jogador da partida ao vivo. Riot ID como o jogo mostra na tela de carregamento (nulo para
    bot ou sem Riot ID); `rankState` UNAVAILABLE quando o elo não pôde ser consultado agora."""

    game_name: str | None = None
    tag_line: str | None = None
    bot: bool = False
    requested: bool = False
    champion_id: int
    champion: ChampionAsset | None = None
    spell1_id: int | None = None
    spell2_id: int | None = None
    keystone_id: int | None = None
    perk_style: int | None = None
    perk_sub_style: int | None = None
    rank_state: Literal["READY", "UNRANKED", "UNAVAILABLE"]
    rank: LivePlayerRank | None = None
    # CC-1214: rota estimada (a Riot não informa ao vivo); nula fora do Summoner's Rift.
    position: Literal["TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY"] | None = None


class LiveBan(CamelModel):
    champion_id: int
    champion: ChampionAsset | None = None


class LiveTeam(CamelModel):
    team_id: Literal[100, 200]
    bans: list[LiveBan]
    participants: list[LiveParticipant]


class LiveGameInfo(CamelModel):
    queue_id: int | None = None
    positions_estimated: bool = False
    rank_queue: Literal["RANKED_SOLO_5x5", "RANKED_FLEX_SR"]
    game_mode: str | None = None
    map_id: int | None = None
    started_at: datetime | None = None
    length_seconds: int
    data_dragon_version: str | None = None
    teams: list[LiveTeam]


class LiveGame(CamelModel):
    """Partida ao vivo (CC-1211). Fora de partida, `state` NOT_IN_GAME e `game` nulo."""

    state: Literal["IN_GAME", "NOT_IN_GAME"]
    game_name: str
    tag_line: str
    checked_at: datetime
    game: LiveGameInfo | None = None


class PlayerLiveStatus(CamelModel):
    """Está jogando agora? (CC-1213). Em partida, a fila, o modo e o início; fora, só o estado."""

    state: Literal["IN_GAME", "NOT_IN_GAME"]
    game_name: str
    tag_line: str
    checked_at: datetime
    queue_id: int | None = None
    game_mode: str | None = None
    started_at: datetime | None = None


class OwnPlayer(CamelModel):
    """Perfil da conta configurada (CC-1202). Sem perfil, `profile` vem nulo e `reason` diz por
    quê: ACCOUNT_NOT_CONFIGURED, SECRET_MISSING, RIOT_KEY_MISSING ou um erro da consulta à Riot
    (PLAYER_NOT_FOUND, PLAYER_NOT_ON_PLATFORM, RIOT_AUTH_FAILED, RIOT_RATE_LIMITED,
    UPSTREAM_UNAVAILABLE). Responde 200 nos dois casos, como /api/v1/ranked/me."""

    profile: PlayerProfile | None = None
    reason: str | None = None


@router.post(
    "/live",
    response_model=LiveGame,
    summary="Partida ao vivo de um Riot ID",
    description=(
        'Como o "Live Game" de sites de estatísticas: Spectator-V5 (BR1) devolve a partida em '
        "andamento, depois da tela de carregamento, com os dez jogadores, campeões, feitiços, "
        "runa principal e banimentos; League-V4 dá o elo de cada um na fila da partida. Sem "
        "placar ao vivo. Até 12 requisições; o resultado fica 20 s em memória e nada é gravado. "
        "O Riot ID vai no corpo, fora dos logs de acesso. Fora de partida: `NOT_IN_GAME`."
    ),
    responses=_ERRORS,
)
async def post_live_game(body: PlayerSyncRequest, request: Request) -> LiveGame:
    return LiveGame.model_validate(
        await live_game(
            request.app.state.session_factory,
            request.app.state.settings,
            body.game_name,
            body.tag_line,
        )
    )


@router.post(
    "/live/status",
    response_model=PlayerLiveStatus,
    summary="O jogador está em partida agora?",
    description=(
        'Aviso "jogando agora" da página do jogador, como o botão ao vivo do op.gg: só '
        "Account-V1 e Spectator-V5 (2 requisições, sem elos), 60 s em memória; nada gravado. "
        "O Riot ID vai no corpo, fora dos logs de acesso."
    ),
    responses=_ERRORS,
)
async def post_live_status(body: PlayerSyncRequest, request: Request) -> PlayerLiveStatus:
    return PlayerLiveStatus.model_validate(
        await live_status(request.app.state.settings, body.game_name, body.tag_line)
    )


@router.get(
    "/me",
    response_model=OwnPlayer,
    summary="Perfil da conta configurada",
    description=(
        "Perfil guardado da conta própria (chip do topo), sem nova consulta à Riot; o Riot ID "
        "vem do arquivo da conta. Só na primeira vez, sem perfil guardado, consulta a Riot como "
        "a busca (3 requisições). Sem perfil, `profile` nulo com o motivo em `reason`."
    ),
)
async def get_own_player(request: Request) -> OwnPlayer:
    from riftlab.auth.request_account import not_linked, own_account
    from riftlab.shared.own_account import NOT_LINKED

    account = await own_account(request)
    if not_linked(request, account):
        return OwnPlayer(reason=NOT_LINKED)
    try:
        profile = await own_player(
            request.app.state.session_factory, request.app.state.settings, account=account
        )
    except NotFoundError:
        return OwnPlayer(reason="ACCOUNT_NOT_CONFIGURED")
    except PlayerLookupError as exc:
        return OwnPlayer(reason=exc.code)
    return OwnPlayer(profile=PlayerProfile.model_validate(profile))


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
