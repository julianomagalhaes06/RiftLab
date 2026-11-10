"""Rotas de conta e sessão (card #10): `/api/v1/auth/*`."""

from __future__ import annotations

import logging
import secrets
from datetime import timedelta
from typing import Any, Literal
from urllib.parse import quote, unquote, urlencode, urlsplit, urlunsplit

import httpx
from fastapi import APIRouter, BackgroundTasks, Query, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import Field

from riftlab.auth import application, social
from riftlab.auth.application import AuthError, AuthUser
from riftlab.auth.mailer import Mailer, OutgoingEmail, deliver
from riftlab.auth.middleware import SESSION_COOKIE
from riftlab.auth.social import SocialName
from riftlab.errors import problem_response
from riftlab.shared.api.common import CamelModel

logger = logging.getLogger("riftlab.auth")
router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

SOCIAL_COOKIE = "riftlab_social"
SOCIAL_COOKIE_PATH = "/api/v1/auth/social"
SOCIAL_COOKIE_SECONDS = 600

MESSAGES = {
    "INVALID_NAME": (422, "Nome inválido", "Use de 2 a 40 caracteres no nome."),
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


class Registration(Credentials):
    name: str = Field(min_length=1, max_length=120)


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
    name: str | None


class SessionState(CamelModel):
    """`authRequired`: o servidor exige login nas áreas da conta. `socialProviders`: os logins
    sociais configurados neste servidor."""

    auth_required: bool
    user: SessionUser | None
    demo_enabled: bool = False
    social_providers: list[SocialName]


def _user(user: AuthUser | None) -> SessionUser | None:
    return SessionUser(id=str(user.id), email=user.email, name=user.name) if user else None


def _session_state(request: Request, user: AuthUser | None) -> SessionState:
    settings = request.app.state.settings
    return SessionState(
        demo_enabled=settings.demo_data_enabled,
        auth_required=settings.auth_required,
        user=_user(user),
        social_providers=social.enabled_providers(settings),
    )


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
    return _session_state(request, request.state.user)


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
    body: Registration, request: Request, background: BackgroundTasks
) -> CodeSent | JSONResponse:
    throttle, ip = request.app.state.mail_throttle, _client_ip(request)
    email = application.normalize_email(body.email)
    try:
        mailer = _mailer(request)
        throttle.check(email, ip)
        outgoing = await application.register(
            request.app.state.session_factory, email, body.password, body.name
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
    return _session_state(request, user)


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
    return _session_state(request, user)


def _with_created_mark(target: str) -> str:
    parts = urlsplit(target)
    query = "&".join(item for item in (parts.query, "conta=nova") if item)
    return urlunsplit(("", "", parts.path, query, parts.fragment))


def _social_failure(reason: str, target: str | None) -> RedirectResponse:
    """Volta à tela de entrada com o motivo, que a interface traduz."""
    query = {"social": reason, **({"next": target} if target else {})}
    response = RedirectResponse(f"/entrar?{urlencode(query)}", status_code=302)
    response.delete_cookie(SOCIAL_COOKIE, path=SOCIAL_COOKIE_PATH)
    return response


@router.get(
    "/social/{provider}/start",
    response_class=RedirectResponse,
    status_code=302,
    summary="Leva a pessoa ao login do provedor",
)
async def get_social_start(
    provider: SocialName, request: Request, target: str | None = Query(None, alias="next")
) -> RedirectResponse:
    settings = request.app.state.settings
    destination = social.safe_next(target)
    keys = social.credentials(settings, provider)
    if keys is None:
        return _social_failure("indisponivel", destination)
    state = secrets.token_urlsafe(32)
    callback = social.redirect_uri(settings, provider)
    response = RedirectResponse(
        social.authorize_url(provider, keys[0], state, callback), status_code=302
    )
    response.set_cookie(
        SOCIAL_COOKIE,
        f"{state}.{quote(destination or '', safe='')}",
        max_age=SOCIAL_COOKIE_SECONDS,
        httponly=True,
        samesite="lax",
        secure=settings.auth_cookie_secure,
        path=SOCIAL_COOKIE_PATH,
    )
    return response


@router.get(
    "/social/{provider}/callback",
    response_class=RedirectResponse,
    status_code=302,
    summary="Recebe a volta do provedor e abre a sessão",
)
async def get_social_callback(
    provider: SocialName,
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    settings = request.app.state.settings
    expected, _, raw_target = request.cookies.get(SOCIAL_COOKIE, "").partition(".")
    target = social.safe_next(unquote(raw_target))
    keys = social.credentials(settings, provider)
    if keys is None:
        return _social_failure("indisponivel", target)
    if not expected or not state or not secrets.compare_digest(state, expected):
        return _social_failure("falhou", target)
    if error or not code:
        return _social_failure("cancelado", target)
    try:
        profile = await social.fetch_profile(
            provider,
            keys,
            code,
            social.redirect_uri(settings, provider),
            request.app.state.social_transport,
        )
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
        logger.warning("login social falhou provider=%s erro=%s", provider, type(exc).__name__)
        return _social_failure("falhou", target)
    if not profile.email or not profile.email_verified:
        return _social_failure("email", target)
    try:
        _, token, created = await application.social_login(
            request.app.state.session_factory,
            provider,
            profile.subject,
            profile.email,
            social.display_name(profile.name),
            lifetime=timedelta(days=settings.auth_session_days),
        )
    except AuthError:
        return _social_failure("falhou", target)
    # Conta nova: boas-vindas ou, se veio de uma página, de volta a ela com um aviso curto.
    if created:
        destination = _with_created_mark(target) if target else "/boas-vindas"
    else:
        destination = target or "/painel"
    response = RedirectResponse(destination, status_code=302)
    response.delete_cookie(SOCIAL_COOKIE, path=SOCIAL_COOKIE_PATH)
    _set_cookie(request, response, token)
    return response


@router.post("/logout", status_code=204, summary="Encerra a sessão atual")
async def post_logout(request: Request) -> Response:
    await application.logout(request.app.state.session_factory, request.cookies.get(SESSION_COOKIE))
    response = Response(status_code=204)
    response.delete_cookie(SESSION_COOKIE, path="/", samesite="strict", httponly=True)
    return response
