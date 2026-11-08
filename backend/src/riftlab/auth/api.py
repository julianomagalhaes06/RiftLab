"""Rotas de conta e sessão (card #10): `/api/v1/auth/*`."""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Literal

from fastapi import APIRouter, BackgroundTasks, Request, Response
from fastapi.responses import JSONResponse
from pydantic import Field

from riftlab.auth import application
from riftlab.auth.application import AuthError, AuthUser
from riftlab.auth.mailer import Mailer, OutgoingEmail, deliver
from riftlab.auth.middleware import SESSION_COOKIE
from riftlab.errors import problem_response
from riftlab.shared.api.common import CamelModel

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

MESSAGES = {
    "INVALID_EMAIL": (422, "E-mail inválido", "Confira o endereço de e-mail."),
    "INVALID_PASSWORD": (
        422,
        "Senha fraca",
        "A senha precisa ter de 8 a 128 caracteres, com uma letra maiúscula e um símbolo.",
    ),
    "INVALID_CREDENTIALS": (401, "Login recusado", "E-mail ou senha incorretos."),
    "EMAIL_NOT_VERIFIED": (403, "E-mail não confirmado", "Confirme seu e-mail para entrar."),
    "INVALID_CODE": (422, "Código inválido", "Código inválido ou vencido. Peça um novo."),
    "LOGIN_RATE_LIMITED": (
        429,
        "Muitas tentativas",
        "Muitas tentativas de login seguidas. Aguarde antes de tentar de novo.",
    ),
    "CODE_RATE_LIMITED": (
        429,
        "Muitos pedidos",
        "Muitos pedidos de código seguidos. Aguarde antes de tentar de novo.",
    ),
    "EMAIL_NOT_CONFIGURED": (
        503,
        "E-mail indisponível",
        "Este servidor não tem envio de e-mail configurado.",
    ),
}


class Credentials(CamelModel):
    email: str = Field(min_length=1, max_length=320)
    password: str = Field(min_length=1, max_length=256)


class EmailOnly(CamelModel):
    email: str = Field(min_length=1, max_length=320)


class VerifyEmail(Credentials):
    code: str = Field(min_length=1, max_length=16)


class ResetPassword(EmailOnly):
    code: str = Field(min_length=1, max_length=16)
    new_password: str = Field(min_length=1, max_length=256)


class CodeSent(CamelModel):
    """Sempre igual: não diz se a conta existe nem se um e-mail saiu de fato."""

    status: Literal["CODE_SENT"] = "CODE_SENT"


class SessionUser(CamelModel):
    id: str
    email: str


class SessionState(CamelModel):
    """`authRequired`: o servidor exige login nas áreas da conta."""

    auth_required: bool
    user: SessionUser | None


def _user(user: AuthUser | None) -> SessionUser | None:
    return SessionUser(id=str(user.id), email=user.email) if user else None


def _error(request: Request, exc: AuthError) -> JSONResponse:
    status, title, detail = MESSAGES[exc.code]
    headers = {"Retry-After": str(exc.retry_after_seconds)} if exc.retry_after_seconds else None
    return problem_response(
        request, status_code=status, code=exc.code, title=title, detail=detail, headers=headers
    )


def _set_cookie(request: Request, response: Response, token: str) -> None:
    settings = request.app.state.settings
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=int(timedelta(days=settings.auth_session_days).total_seconds()),
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


def _mailer(request: Request) -> Mailer:
    mailer: Mailer | None = request.app.state.mailer
    if mailer is None:
        raise AuthError("EMAIL_NOT_CONFIGURED")
    return mailer


def _queue_email(background: BackgroundTasks, mailer: Mailer, email: OutgoingEmail | None) -> None:
    if email is not None:
        background.add_task(deliver, mailer, email)


_RATE: dict[int | str, dict[str, Any]] = {429: {"description": "Muitos pedidos"}}


@router.post(
    "/register",
    response_model=CodeSent,
    status_code=202,
    summary="Cria a conta e envia o código de confirmação",
    description="A resposta é a mesma se o e-mail já tiver conta: nesse caso, ele recebe um aviso.",
    responses={422: {"description": "Dados inválidos"}, **_RATE},
)
async def post_register(
    body: Credentials, request: Request, background: BackgroundTasks
) -> CodeSent | JSONResponse:
    throttle, ip = request.app.state.mail_throttle, _client_ip(request)
    email = application.normalize_email(body.email)
    try:
        mailer = _mailer(request)
        throttle.check(email, ip)
        outgoing = await application.register(
            request.app.state.session_factory, email, body.password
        )
    except AuthError as exc:
        return _error(request, exc)
    throttle.failed(email, ip)
    _queue_email(background, mailer, outgoing)
    return CodeSent()


@router.post(
    "/verify-email",
    response_model=SessionState,
    summary="Confirma o e-mail com o código e abre a sessão",
    responses={422: {"description": "Código inválido"}, **_RATE},
)
async def post_verify_email(
    body: VerifyEmail, request: Request, response: Response
) -> SessionState | JSONResponse:
    settings = request.app.state.settings
    throttle, ip = request.app.state.code_throttle, _client_ip(request)
    email = application.normalize_email(body.email)
    try:
        throttle.check(email, ip)
        user, token = await application.verify_email(
            request.app.state.session_factory,
            email,
            body.code,
            body.password,
            lifetime=timedelta(days=settings.auth_session_days),
        )
    except AuthError as exc:
        if exc.code == "INVALID_CODE":
            throttle.failed(email, ip)
        return _error(request, exc)
    throttle.succeeded(email)
    _set_cookie(request, response, token)
    return SessionState(auth_required=settings.auth_required, user=_user(user))


@router.post(
    "/resend-code",
    response_model=CodeSent,
    status_code=202,
    summary="Reenvia o código de confirmação",
    responses=_RATE,
)
async def post_resend_code(
    body: EmailOnly, request: Request, background: BackgroundTasks
) -> CodeSent | JSONResponse:
    throttle, ip = request.app.state.mail_throttle, _client_ip(request)
    email = application.normalize_email(body.email)
    try:
        mailer = _mailer(request)
        throttle.check(email, ip)
    except AuthError as exc:
        return _error(request, exc)
    throttle.failed(email, ip)
    outgoing = await application.resend_verification(request.app.state.session_factory, email)
    _queue_email(background, mailer, outgoing)
    return CodeSent()


@router.post(
    "/password/forgot",
    response_model=CodeSent,
    status_code=202,
    summary="Envia o código para redefinir a senha",
    description="A resposta é a mesma exista ou não conta com o e-mail.",
    responses=_RATE,
)
async def post_forgot_password(
    body: EmailOnly, request: Request, background: BackgroundTasks
) -> CodeSent | JSONResponse:
    throttle, ip = request.app.state.mail_throttle, _client_ip(request)
    email = application.normalize_email(body.email)
    try:
        mailer = _mailer(request)
        throttle.check(email, ip)
    except AuthError as exc:
        return _error(request, exc)
    throttle.failed(email, ip)
    outgoing = await application.forgot_password(request.app.state.session_factory, email)
    _queue_email(background, mailer, outgoing)
    return CodeSent()


@router.post(
    "/password/reset",
    status_code=204,
    summary="Troca a senha com o código e encerra todas as sessões da conta",
    responses={422: {"description": "Código inválido ou senha fraca"}, **_RATE},
)
async def post_reset_password(body: ResetPassword, request: Request) -> Response:
    throttle, ip = request.app.state.code_throttle, _client_ip(request)
    email = application.normalize_email(body.email)
    try:
        throttle.check(email, ip)
        await application.reset_password(
            request.app.state.session_factory, email, body.code, body.new_password
        )
    except AuthError as exc:
        if exc.code == "INVALID_CODE":
            throttle.failed(email, ip)
        return _error(request, exc)
    throttle.succeeded(email)
    return Response(status_code=204)


@router.post(
    "/login",
    response_model=SessionState,
    summary="Entra com e-mail e senha",
    responses={
        401: {"description": "Recusado"},
        403: {"description": "E-mail não confirmado"},
        429: {"description": "Muitas tentativas"},
    },
)
async def post_login(
    body: Credentials, request: Request, response: Response
) -> SessionState | JSONResponse:
    settings = request.app.state.settings
    throttle = request.app.state.login_throttle
    email, ip = application.normalize_email(body.email), _client_ip(request)
    try:
        throttle.check(email, ip)
        user, token = await application.login(
            request.app.state.session_factory,
            email,
            body.password,
            lifetime=timedelta(days=settings.auth_session_days),
        )
    except AuthError as exc:
        if exc.code == "INVALID_CREDENTIALS":
            throttle.failed(email, ip)
        return _error(request, exc)
    throttle.succeeded(email)
    _set_cookie(request, response, token)
    return SessionState(auth_required=settings.auth_required, user=_user(user))


@router.post("/logout", status_code=204, summary="Encerra a sessão atual")
async def post_logout(request: Request) -> Response:
    await application.logout(request.app.state.session_factory, request.cookies.get(SESSION_COOKIE))
    response = Response(status_code=204)
    response.delete_cookie(SESSION_COOKIE, path="/", samesite="strict", httponly=True)
    return response
