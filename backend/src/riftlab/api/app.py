from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from riftlab import db
from riftlab.api.loopback import LoopbackGuardMiddleware
from riftlab.api.routes.analyses import router as analyses_router
from riftlab.api.routes.champions import router as champions_router
from riftlab.api.routes.decisions import router as decisions_router
from riftlab.api.routes.health import router as health_router
from riftlab.api.routes.imports import router as imports_router
from riftlab.api.routes.learning import router as learning_router
from riftlab.api.routes.matches import router as matches_router
from riftlab.api.routes.objectives import router as objectives_router
from riftlab.auth.api import router as auth_router
from riftlab.auth.application import LoginThrottle
from riftlab.auth.middleware import AuthMiddleware
from riftlab.catalog.api import router as catalog_router
from riftlab.config import Settings, get_settings
from riftlab.dashboard.api import router as dashboard_router
from riftlab.errors import register_exception_handlers
from riftlab.history_sync.api import router as history_sync_router
from riftlab.match_review.api import router as match_review_router
from riftlab.observability import RequestIdMiddleware, configure_logging
from riftlab.players.api import router as players_router
from riftlab.ranked.api import router as ranked_router
from riftlab.shared.db import build_session_factory

logger = logging.getLogger("riftlab.api")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = settings
        app.state.db_engine = db.build_engine(settings.database_url)
        app.state.session_factory = build_session_factory(app.state.db_engine)
        app.state.login_throttle = LoginThrottle()
        logger.info("riftlab api iniciada modo=%s", "DEMO" if settings.demo_mode else "REAL")
        try:
            yield
        finally:
            await app.state.db_engine.dispose()

    # Swagger UI/ReDoc só em desenvolvimento (CC-1220): as páginas carregam JS de CDN sem SRI.
    # O schema (`/openapi.json`) segue servido; o versionado sai de `cc openapi export`.
    docs = settings.environment == "development"
    app = FastAPI(
        title="RiftLab API",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs" if docs else None,
        redoc_url="/redoc" if docs else None,
    )
    # O último adicionado envolve os outros: o request id já existe quando a guarda recusa.
    # A sessão (cards #10/#11) fica por dentro da guarda do loopback.
    app.add_middleware(AuthMiddleware)
    app.add_middleware(LoopbackGuardMiddleware, allowed_hosts=settings.api_allowed_hosts)
    app.add_middleware(RequestIdMiddleware)
    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(champions_router)
    app.include_router(imports_router)
    app.include_router(matches_router)
    app.include_router(decisions_router)
    app.include_router(match_review_router)
    app.include_router(players_router)
    app.include_router(catalog_router)
    app.include_router(ranked_router)
    app.include_router(history_sync_router)
    app.include_router(dashboard_router)
    app.include_router(analyses_router)
    app.include_router(learning_router)
    app.include_router(objectives_router)
    register_exception_handlers(app)

    return app
