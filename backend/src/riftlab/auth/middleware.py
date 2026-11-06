"""Sessão e CSRF nas rotas da API (cards #10 e #11).

Para toda rota `/api/v1/*`, resolve o usuário do cookie de sessão e o deixa em
`request.state.user` (ou None). Com `auth_required`:
- método que altera dados sem o cabeçalho `X-RiftLab-Request` ⇒ 403 CSRF_HEADER_MISSING.
  Formulário ou página de outro site não consegue enviar esse cabeçalho sem CORS, que a API
  não libera; o cookie também é SameSite=Strict;
- sem sessão válida ⇒ 401 AUTH_REQUIRED, exceto em `/api/v1/auth/*` e na leitura pública.

Leitura pública, como em sites de estatísticas do jogo: sem conta, dá para buscar qualquer
jogador, atualizar o histórico dele (com o intervalo mínimo da sincronização), abrir a lista de
partidas dele, a partida (placar, linha do tempo, revisão e decisões), o detalhe de uma decisão,
a partida ao vivo, os campeões e o catálogo. Exigem conta tudo o que cria ou guarda algo da
pessoa (análise, estudo, objetivo, relatório, feedback), o Painel, a posição e o histórico da
conta própria, a importação avulsa, o vínculo da conta Riot e as remoções.
Health, OpenAPI e demais caminhos fora de `/api/v1` não passam por aqui.
"""

from __future__ import annotations

import re
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
_UUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
_MATCH = r"[A-Za-z0-9_-]{1,64}"
# (métodos, caminho completo) liberados sem conta. Os parâmetros continuam validados nas rotas.
PUBLIC_ROUTES: tuple[tuple[frozenset[str], re.Pattern[str]], ...] = (
    (frozenset({"GET"}), re.compile(r"/api/v1/catalog/current")),
    (frozenset({"GET"}), re.compile(r"/api/v1/champions/patterns")),
    (frozenset({"POST"}), re.compile(r"/api/v1/players/(?:lookup|live|live/status)")),
    (frozenset({"GET", "POST"}), re.compile(rf"/api/v1/players/{_UUID}/sync")),
    (frozenset({"GET"}), re.compile(rf"/api/v1/matches/{_MATCH}(?:/review|/timeline|/decisions)?")),
    (frozenset({"POST"}), re.compile(rf"/api/v1/matches/{_MATCH}/preparation")),
    (frozenset({"GET"}), re.compile(rf"/api/v1/decisions/{_UUID}")),
)


def is_public(request: Request) -> bool:
    """Rota de leitura liberada sem conta. A lista de partidas só vale para um jogador buscado
    (`?player=`): sem ele, a lista é a da conta própria, que exige login."""
    path, method = request.url.path, request.method
    if path.startswith(PUBLIC_PREFIX):
        return True
    if method == "GET" and path == "/api/v1/matches":
        return bool(request.query_params.get("player"))
    return any(method in methods and pattern.fullmatch(path) for methods, pattern in PUBLIC_ROUTES)


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
        if request.state.user is None and not is_public(request):
            return problem_response(
                request,
                status_code=401,
                code="AUTH_REQUIRED",
                title="Entre para continuar",
                detail="Crie uma conta ou entre para analisar e guardar suas partidas.",
            )
        return await call_next(request)
