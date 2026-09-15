from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from riftlab import db
from riftlab.api.routes.health import router as health_router
from riftlab.api.routes.imports import router as imports_router
from riftlab.config import Settings, get_settings
from riftlab.errors import register_exception_handlers
from riftlab.observability import RequestIdMiddleware, configure_logging
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
        logger.info("riftlab api iniciada modo=%s", "DEMO" if settings.demo_mode else "REAL")
        try:
            yield
        finally:
            await app.state.db_engine.dispose()

    app = FastAPI(title="RiftLab API", version="0.1.0", lifespan=lifespan)
    app.add_middleware(RequestIdMiddleware)
    app.include_router(health_router)
    app.include_router(imports_router)
    register_exception_handlers(app)

    return app
