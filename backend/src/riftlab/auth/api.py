"""Rotas de conta e sessão (card #10): `/api/v1/auth/*`."""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from pydantic import Field

from riftlab.auth import application
from riftlab.auth.application import AuthError, AuthUser
from riftlab.auth.middleware import SESSION_COOKIE
from riftlab.errors import problem_response
from riftlab.shared.api.common import CamelModel

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

MESSAGES = {
    "INVALID_USERNAME": (
        422,
        "Nome de usuário inválido",
        "Use de 3 a 32 caracteres: letras, números, ponto, hífen ou sublinhado.",
    ),
    "INVALID_PASSWORD": (422, "Senha inválida", "A senha precisa ter de 10 a 128 caracteres."),
    "USERNAME_TAKEN": (409, "Nome em uso", "Esse nome de usuário já está em uso."),
    "INVALID_CREDENTIALS": (401, "Login recusado", "Usuário ou senha incorretos."),
    "LOGIN_RATE_LIMITED": (
        429,
        "Muitas tentativas",
        "Muitas tentativas de login seguidas. Aguarde antes de tentar de novo.",
    ),
}


class Credentials(CamelModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class SessionUser(CamelModel):
    """`guest`: visitante, restrito à demonstração (card #14)."""

    id: str
    username: str
    guest: bool = False


class SessionState(CamelModel):
    """`authRequired`: o servidor exige login nas rotas da aplicação."""

    auth_required: bool
    user: SessionUser | None


def _user(user: AuthUser | None) -> SessionUser | None:
    return SessionUser(id=str(user.id), username=user.username, guest=user.guest) if user else None


def _error(request: Request, exc: AuthError) -> JSONResponse:
    status, title, detail = MESSAGES[exc.code]
    headers = {"Retry-After": str(exc.retry_after_seconds)} if exc.retry_after_seconds else None
    return problem_response(
        request, status_code=status, code=exc.code, title=title, detail=detail, headers=headers
    )


def _set_cookie(
    request: Request, response: Response, token: str, lifetime: timedelta | None = None
) -> None:
    settings = request.app.state.settings
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=int((lifetime or timedelta(days=settings.auth_session_days)).total_seconds()),
        httponly=True,
        samesite="strict",
        secure=settings.auth_cookie_secure,
        path="/",
    )


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "-"


@router.get("/session", response_model=SessionState, summary="Sessão atual")
async def get_session(request: Request) -> SessionState:
    return SessionState(
        auth_required=request.app.state.settings.auth_required, user=_user(request.state.user)
    )


@router.post(
    "/register",
    response_model=SessionState,
    status_code=201,
    summary="Cria a conta local e abre a sessão",
    responses={409: {"description": "Nome em uso"}, 422: {"description": "Dados inválidos"}},
)
async def post_register(
    body: Credentials, request: Request, response: Response
) -> SessionState | JSONResponse:
    settings = request.app.state.settings
    try:
        user, token = await application.register(
            request.app.state.session_factory,
            body.username.strip(),
            body.password,
            lifetime=timedelta(days=settings.auth_session_days),
        )
    except AuthError as exc:
        return _error(request, exc)
    _set_cookie(request, response, token)
    return SessionState(auth_required=settings.auth_required, user=_user(user))


@router.post(
    "/login",
    response_model=SessionState,
    summary="Entra com usuário e senha",
    responses={401: {"description": "Recusado"}, 429: {"description": "Muitas tentativas"}},
)
async def post_login(
    body: Credentials, request: Request, response: Response
) -> SessionState | JSONResponse:
    settings = request.app.state.settings
    throttle = request.app.state.login_throttle
    username, ip = body.username.strip(), _client_ip(request)
    try:
        throttle.check(username, ip)
        user, token = await application.login(
            request.app.state.session_factory,
            username,
            body.password,
            lifetime=timedelta(days=settings.auth_session_days),
        )
    except AuthError as exc:
        if exc.code == "INVALID_CREDENTIALS":
            throttle.failed(username, ip)
        return _error(request, exc)
    throttle.succeeded(username)
    _set_cookie(request, response, token)
    return SessionState(auth_required=settings.auth_required, user=_user(user))


@router.post(
    "/guest",
    response_model=SessionState,
    status_code=201,
    summary="Entra como visitante, só com a demonstração",
)
async def post_guest(request: Request, response: Response) -> SessionState:
    user, token = await application.create_guest(request.app.state.session_factory)
    _set_cookie(request, response, token, application.GUEST_LIFETIME)
    return SessionState(auth_required=request.app.state.settings.auth_required, user=_user(user))


@router.post("/logout", status_code=204, summary="Encerra a sessão atual")
async def post_logout(request: Request) -> Response:
    await application.logout(request.app.state.session_factory, request.cookies.get(SESSION_COOKIE))
    response = Response(status_code=204)
    response.delete_cookie(SESSION_COOKIE, path="/", samesite="strict", httponly=True)
    return response
