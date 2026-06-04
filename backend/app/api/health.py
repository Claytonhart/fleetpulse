"""Health check — confirms the API is up and the DB answers."""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import text

from app.api.deps import SessionDep
from app.schemas.health import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health(session: SessionDep) -> HealthResponse:
    await session.execute(text("SELECT 1"))
    return HealthResponse(status="ok", db="ok")
