"""Async engine + session factory.

Sized deliberately from settings; `pool_timeout` is the acquire bound that Step 4
surfaces as a 503 under pool exhaustion (back-pressure, §10.9). The per-request
session *dependency* is added in Step 3 — this module just owns the engine and the
sessionmaker.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import settings

engine: AsyncEngine = create_async_engine(
    settings.database_url,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_timeout=settings.db_pool_acquire_timeout_seconds,
    pool_pre_ping=True,
)

async_session_maker: async_sessionmaker[AsyncSession] = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)
