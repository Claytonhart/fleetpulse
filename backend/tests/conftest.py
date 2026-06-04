"""Shared pytest fixtures — the canonical test-DB harness for the whole project.

Established in Step 1; **every later step reuses this — do NOT stand up your own.**

What it does:
  * `db_container` / `redis_container` (session-scoped): start ephemeral
    TimescaleDB + Redis via `testcontainers`, using the SAME pinned images as
    Compose, so tests run identically locally and in CI (both only need Docker).
  * `db_settings` (session-scoped): once migrations exist (Step 2), this fixture
    runs `alembic upgrade head` against the throwaway DB before yielding. Until
    then it yields the raw connection settings so the Step 1 harness smoke test
    (`SELECT 1`) can prove the plumbing works before any real schema exists.
  * `db_engine` / `db_conn` (function-scoped): per-test async engine + connection
    with rollback isolation, so tests don't leak state into each other.
  * `seed_readings`: a tiny data-seeding helper later integration tests reuse to
    insert N telemetry readings for a vehicle (direct insert, not via the
    simulator — tests must self-seed and never depend on the simulator).

Image tags are kept in sync with docker-compose.yml on purpose.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Iterator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
from testcontainers.postgres import PostgresContainer
from testcontainers.redis import RedisContainer

# Keep these in lockstep with docker-compose.yml.
TIMESCALE_IMAGE = "timescale/timescaledb:2.27.2-pg16"
REDIS_IMAGE = "redis:7.4"


@pytest.fixture(scope="session")
def db_container() -> Iterator[PostgresContainer]:
    """Ephemeral TimescaleDB for the whole test session."""
    with PostgresContainer(
        TIMESCALE_IMAGE,
        username="fleetpulse",
        password="fleetpulse",
        dbname="fleetpulse",
        driver="asyncpg",
    ) as container:
        yield container


@pytest.fixture(scope="session")
def redis_container() -> Iterator[RedisContainer]:
    """Ephemeral Redis for the whole test session."""
    with RedisContainer(REDIS_IMAGE) as container:
        yield container


@pytest.fixture(scope="session")
def database_url(db_container: PostgresContainer) -> str:
    """Async SQLAlchemy URL for the throwaway DB (asyncpg driver)."""
    return str(db_container.get_connection_url())


@pytest.fixture(scope="session")
def redis_url(redis_container: RedisContainer) -> str:
    """Redis URL for the throwaway broker/cache."""
    host = redis_container.get_container_host_ip()
    port = redis_container.get_exposed_port(6379)
    return f"redis://{host}:{port}/0"


@pytest.fixture(scope="session")
def db_settings(database_url: str) -> dict[str, str]:
    """Connection settings, with the schema applied.

    Step 2 adds Alembic migrations; at that point this fixture should run
    `alembic upgrade head` against `database_url` here (once, session-scoped)
    before yielding, so every integration test sees the full schema. The hook is
    intentionally left as a single obvious place to add that call.
    """
    # TODO(Step 2): apply migrations here —
    #   alembic.config.main(["-x", f"db_url={database_url}", "upgrade", "head"])
    return {"database_url": database_url}


@pytest_asyncio.fixture
async def db_engine(database_url: str) -> AsyncIterator[AsyncEngine]:
    """Per-test async engine against the session's throwaway DB."""
    engine = create_async_engine(database_url, future=True)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def db_conn(db_engine: AsyncEngine) -> AsyncIterator[AsyncConnection]:
    """Per-test connection in a transaction that always rolls back (isolation)."""
    async with db_engine.connect() as conn:
        trans = await conn.begin()
        try:
            yield conn
        finally:
            await trans.rollback()


@pytest_asyncio.fixture
async def seed_readings(
    db_conn: AsyncConnection,
) -> Callable[..., Awaitable[None]]:
    """Insert N telemetry readings for a vehicle directly (no simulator).

    Returns an async callable integration tests reuse. The schema does not exist
    until Step 2; this is the reusable entry point those tests will call.
    """

    async def _seed(*_args: object, **_kwargs: object) -> None:  # pragma: no cover
        raise NotImplementedError(
            "seed_readings is wired in Step 2+ once telemetry_reading exists; "
            "implement the INSERT against db_conn here."
        )

    return _seed
