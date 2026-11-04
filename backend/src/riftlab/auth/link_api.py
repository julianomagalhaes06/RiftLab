"""Rotas do vínculo da conta Riot (card #12): `/api/v1/riot-link`. Sempre exigem sessão."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from pydantic import Field

from riftlab.auth import riot_link
from riftlab.auth.riot_link import RiotLinkError
from riftlab.errors import problem_response
from riftlab.shared.api.common import CamelModel

router = APIRouter(prefix="/api/v1/riot-link", tags=["auth"])

MESSAGES = {
    "AUTH_REQUIRED": (401, "Entre para continuar", "Faça login para vincular sua conta Riot."),
    "NO_CHALLENGE": (
        409,
        "Sem desafio",
        "Peça um ícone para comprovar a conta antes de verificar.",
    ),
    "CHALLENGE_EXPIRED": (
        409,
        "Desafio expirado",
        "O prazo de 10 minutos terminou. Peça um novo ícone para tentar de novo.",
    ),
    "ICON_MISMATCH": (
        409,
        "Ícone diferente",
        "O ícone de perfil ainda não é o pedido. Troque-o no cliente do jogo e verifique de novo.",
    ),
    "ACCOUNT_ALREADY_LINKED": (
        409,
        "Conta já vinculada",
        "Essa conta Riot já está vinculada a outro usuário desta instalação.",
    ),
}


class RiotIdBody(CamelModel):
    game_name: str = Field(min_length=1, max_length=64)
    tag_line: str = Field(min_length=1, max_length=16)


class RiotLinkState(CamelModel):
    """`NONE`: sem vínculo; `PENDING`: desafio ativo (`iconId` até `expiresAt`); `LINKED`."""

    state: Literal["NONE", "PENDING", "LINKED"]
    game_name: str | None = None
    tag_line: str | None = None
    icon_id: int | None = None
    expires_at: datetime | None = None
    linked_at: datetime | None = None


def _error(request: Request, code: str) -> JSONResponse:
    status, title, detail = MESSAGES[code]
    return problem_response(request, status_code=status, code=code, title=title, detail=detail)


@router.get("", response_model=RiotLinkState, summary="Vínculo da conta Riot do usuário")
async def get_link(request: Request) -> RiotLinkState | JSONResponse:
    user = request.state.user
    if user is None:
        return _error(request, "AUTH_REQUIRED")
    state = await riot_link.link_state(request.app.state.session_factory, user.id)
    return RiotLinkState.model_validate(state)


@router.post(
    "/challenge",
    response_model=RiotLinkState,
    summary="Sorteia o ícone que comprova a conta (Account-V1 e Summoner-V4)",
)
async def post_challenge(body: RiotIdBody, request: Request) -> RiotLinkState | JSONResponse:
    user = request.state.user
    if user is None:
        return _error(request, "AUTH_REQUIRED")
    try:
        state = await riot_link.start_challenge(
            request.app.state.session_factory,
            request.app.state.settings,
            user.id,
            body.game_name,
            body.tag_line,
        )
    except RiotLinkError as exc:
        return _error(request, exc.code)
    return RiotLinkState.model_validate(state)


@router.post("/verify", response_model=RiotLinkState, summary="Confere o ícone e vincula")
async def post_verify(request: Request) -> RiotLinkState | JSONResponse:
    user = request.state.user
    if user is None:
        return _error(request, "AUTH_REQUIRED")
    try:
        state = await riot_link.verify_challenge(
            request.app.state.session_factory, request.app.state.settings, user.id
        )
    except RiotLinkError as exc:
        return _error(request, exc.code)
    return RiotLinkState.model_validate(state)


@router.delete("", status_code=204, summary="Desfaz o vínculo da conta Riot")
async def delete_link(request: Request) -> Response:
    user = request.state.user
    if user is None:
        return _error(request, "AUTH_REQUIRED")
    await riot_link.unlink(request.app.state.session_factory, user.id)
    return Response(status_code=204)
