"""Sessão e CSRF nas rotas da API (cards #10 e #11).

Para toda rota `/api/v1/*`, resolve o usuário do cookie de sessão e o deixa em
`request.state.user` (ou None). Com `auth_required`:
- sem sessão válida ⇒ 401 AUTH_REQUIRED, exceto em `/api/v1/auth/*`;
- método que altera dados sem o cabeçalho `X-RiftLab-Request` ⇒ 403 CSRF_HEADER_MISSING.
  Formulário ou página de outro site não consegue enviar esse cabeçalho sem CORS, que a API
  não libera; o cookie também é SameSite=Strict.
Health, OpenAPI e demais caminhos fora de `/api/v1` não passam por aqui.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from riftlab.auth.application import user_for_token
from riftlab.errors import problem_response

SESSION_COOKIE = "riftlab_session"
CSRF_HEADER = "x-riftlab-request"
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
API_PREFIX = "/api/v1/"
PUBLIC_PREFIX = "/api/v1/auth/"


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request.state.user = None
        path = request.url.path
        if not path.startswith(API_PREFIX):
            return await call_next(request)
        settings = request.app.state.settings
        token = request.cookies.get(SESSION_COOKIE)
        if token:
            request.state.user = await user_for_token(request.app.state.session_factory, token)
        if not settings.auth_required:
            return await call_next(request)
        if request.method in UNSAFE_METHODS and not request.headers.get(CSRF_HEADER):
            return problem_response(
                request,
                status_code=403,
                code="CSRF_HEADER_MISSING",
                title="Requisição sem origem confirmada",
                detail="A API só aceita alterações enviadas pela interface do RiftLab.",
            )
        if request.state.user is None and not path.startswith(PUBLIC_PREFIX):
            return problem_response(
                request,
                status_code=401,
                code="AUTH_REQUIRED",
                title="Entre para continuar",
                detail="Faça login para acessar o RiftLab.",
            )
        return await call_next(request)
