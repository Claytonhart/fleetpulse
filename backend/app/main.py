"""FastAPI application factory.

`create_app()` is referenced by uvicorn with `--factory` (see the Dockerfile /
Compose `api` service). This service does NOT run migrations — the one-shot
`migrate` Compose service owns `alembic upgrade head` (migration ownership).
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import api_router
from app.config import settings
from app.log_config import configure_logging


def create_app() -> FastAPI:
    configure_logging()

    app = FastAPI(title="FleetPulse API", version="0.1.0")

    # Cross-origin: the Step 11 web app is served from a different origin.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(api_router, prefix="/api/v1")
    return app
