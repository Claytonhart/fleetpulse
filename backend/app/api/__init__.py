"""API package — the aggregated `/api/v1` router.

Later steps add their routers (telemetry, vehicles, incidents, rules) here.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api import health, telemetry

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(telemetry.router)

__all__ = ["api_router"]
