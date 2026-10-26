"""Reforço da fronteira do loopback (CC-1215).

A API não tem autenticação: por desenho, só o operador na própria estação a alcança. Este
middleware reforça essa premissa contra o que chega ao loopback sem ser o operador:

- `Host` fora da lista configurada (DNS rebinding, conexão vinda da rede local) → 400;
- `Sec-Fetch-Site` `cross-site`/`same-site` (página de outro site no navegador do operador:
  `<object>`, `<img>`, formulário, navegação) → 403. Ausente (CLI, curl, testes) ou
  `same-origin`/`none` (a própria SPA, pelo proxy do Vite; endereço digitado) passa;
- toda resposta sai com anti-framing (`X-Frame-Options`, `frame-ancestors`) e `nosniff`,
  somando `frame-ancestors` a uma CSP que a rota já tenha definido (relatório HTML).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from riftlab.errors import problem_response

CROSS_SITE = frozenset({"cross-site", "same-site"})
FRAME_ANCESTORS = "frame-ancestors 'none'"


def host_name(header: str) -> str:
    """Nome do `Host` sem porta, em minúsculas (`[::1]:8000` → `::1`)."""
    value = header.strip().lower()
    if value.startswith("["):
        return value[1 : value.find("]")] if "]" in value else ""
    return value.rsplit(":", 1)[0] if value.count(":") == 1 else value


def _protect(response: Response) -> Response:
    response.headers["X-Frame-Options"] = "DENY"
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    csp = response.headers.get("Content-Security-Policy")
    if csp is None:
        response.headers["Content-Security-Policy"] = FRAME_ANCESTORS
    elif "frame-ancestors" not in csp:
        response.headers["Content-Security-Policy"] = f"{csp}; {FRAME_ANCESTORS}"
    return response


class LoopbackGuardMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp, allowed_hosts: Iterable[str]) -> None:
        super().__init__(app)
        self.allowed_hosts = frozenset(h.strip().lower() for h in allowed_hosts)

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if host_name(request.headers.get("host", "")) not in self.allowed_hosts:
            return _protect(
                problem_response(
                    request,
                    status_code=400,
                    code="HOST_NOT_ALLOWED",
                    title="Host não permitido",
                    detail="A API só atende pelo endereço local da estação.",
                )
            )
        if request.headers.get("sec-fetch-site", "").lower() in CROSS_SITE:
            return _protect(
                problem_response(
                    request,
                    status_code=403,
                    code="CROSS_SITE_REQUEST",
                    title="Requisição de outro site",
                    detail="A API só aceita requisições da própria interface do RiftLab.",
                )
            )
        return _protect(await call_next(request))
