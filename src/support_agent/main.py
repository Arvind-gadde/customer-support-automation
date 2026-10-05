"""ASGI entrypoint: `uvicorn --factory support_agent.main:create_app`.

An app FACTORY (not a module-level `app`) means importing this module has no
side effects - tests build apps with their own settings and fakes.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import FastAPI

from support_agent import __version__
from support_agent.api.middleware import RequestContextMiddleware
from support_agent.api.routes import chat, health, threads
from support_agent.bootstrap import RuntimeFactory, build_runtime
from support_agent.core.config import Settings, get_settings
from support_agent.core.errors import register_exception_handlers
from support_agent.core.logging import configure_logging, get_logger
from support_agent.core.telemetry import configure_telemetry

logger = get_logger(__name__)


def create_app(
    settings: Settings | None = None, runtime_factory: RuntimeFactory = build_runtime
) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_json)
    configure_telemetry(settings)  # after logging: it appends an OTel log handler

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with AsyncExitStack() as stack:
            app.state.runtime = await runtime_factory(settings, stack)
            logger.info("app.started", version=__version__, environment=settings.environment)
            yield
            logger.info("app.stopping")

    app = FastAPI(
        title="Support Agent API",
        version=__version__,
        lifespan=lifespan,
        # Don't publish the API surface in production.
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None,
        openapi_url=None if settings.is_production else "/openapi.json",
    )
    app.state.settings = settings
    app.add_middleware(RequestContextMiddleware)
    register_exception_handlers(app)
    app.include_router(health.router)
    app.include_router(chat.router)
    app.include_router(threads.router)
    return app
