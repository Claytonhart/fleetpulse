"""FastAPI application factory.

`create_app()` is referenced by uvicorn with `--factory` (see the Dockerfile /
Compose `api` service). This service does NOT run migrations — the one-shot
`migrate` Compose service owns `alembic upgrade head` (migration ownership).
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.exc import TimeoutError as SQLAlchemyPoolTimeout
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.api import api_router
from app.config import settings
from app.log_config import configure_logging
from app.middleware import BodySizeLimitMiddleware


async def _pool_timeout_handler(_request: Request, _exc: Exception) -> JSONResponse:
    # DB pool acquire timed out (back-pressure, §10.9) — shed load, don't block.
    return JSONResponse(
        status_code=503,
        content={"detail": "database connection pool exhausted"},
        headers={"Retry-After": "1"},
    )


def create_app() -> FastAPI:
    configure_logging()

    app = FastAPI(title="FleetPulse API", version="0.1.0")

    # Request-body cap (§10.9) → 413. Must be ASGI middleware (no Starlette built-in).
    app.add_middleware(BodySizeLimitMiddleware, max_body_bytes=settings.max_body_bytes)

    # Cross-origin: the Step 11 web app is served from a different origin.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.add_exception_handler(SQLAlchemyPoolTimeout, _pool_timeout_handler)

    app.include_router(api_router, prefix="/api/v1")
    return app
