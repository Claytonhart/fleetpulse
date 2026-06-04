"""Step 1 harness smoke test.

Proves the testcontainers-backed test-DB harness (conftest.py) actually stands up
a TimescaleDB + Redis and that we can talk to them — BEFORE any real schema or
tests depend on it. Later steps replace/augment this with real integration tests.
"""

from __future__ import annotations

import redis.asyncio as aioredis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection


async def test_db_select_one(db_conn: AsyncConnection) -> None:
    """The throwaway DB answers a trivial query through the shared fixture."""
    result = await db_conn.execute(text("SELECT 1"))
    assert result.scalar_one() == 1


async def test_timescaledb_extension_available(db_conn: AsyncConnection) -> None:
    """The image is really TimescaleDB — the extension is available to CREATE."""
    result = await db_conn.execute(
        text("SELECT name FROM pg_available_extensions WHERE name = 'timescaledb'")
    )
    assert result.scalar_one() == "timescaledb"


async def test_redis_ping(redis_url: str) -> None:
    """The throwaway Redis responds to PING."""
    client: aioredis.Redis = aioredis.from_url(redis_url)  # type: ignore[no-untyped-call]
    try:
        assert await client.ping() is True
    finally:
        await client.aclose()
