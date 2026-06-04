"""Shared pytest fixtures — the canonical test-DB harness for the whole project.

Established in Step 1; **every later step reuses this — do NOT stand up your own.**

What it does:
  * `db_container` / `redis_container` (session-scoped): start ephemeral
    TimescaleDB + Redis via `testcontainers`, using the SAME pinned images as
    Compose, so tests run identically locally and in CI (both only need Docker).
  * `db_settings` (session-scoped): runs `alembic upgrade head` against the
    throwaway DB once per session before yielding connection settings, so every
    integration test that depends on it sees the full schema.
  * `db_engine` / `db_conn` (function-scoped): per-test async engine + connection
    with rollback isolation, so tests don't leak state into each other.
  * `seed_readings`: a tiny data-seeding helper later integration tests reuse to
    insert N telemetry readings for a vehicle (direct insert, not via the
    simulator — tests must self-seed and never depend on the simulator).

Image tags are kept in sync with docker-compose.yml on purpose.
"""

from __future__ import annotations

import os
import pathlib
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from testcontainers.postgres import PostgresContainer
from testcontainers.redis import RedisContainer

# Keep these in lockstep with docker-compose.yml.
TIMESCALE_IMAGE = "timescale/timescaledb:2.27.2-pg16"
REDIS_IMAGE = "redis:7.4"

# backend/ — where alembic.ini lives (script_location uses %(here)s under it).
BACKEND_DIR = pathlib.Path(__file__).resolve().parents[1]


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
    """Connection settings, with the full schema applied via `alembic upgrade head`.

    Runs once per session against the throwaway DB. env.py reads the URL from the
    `ALEMBIC_DB_URL` env var (precedence: -x db_url > ALEMBIC_DB_URL > settings).
    Integration tests depend on this fixture to guarantee the schema exists.
    """
    os.environ["ALEMBIC_DB_URL"] = database_url
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    command.upgrade(cfg, "head")
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
async def db_conn(
    db_settings: dict[str, str], db_engine: AsyncEngine
) -> AsyncIterator[AsyncConnection]:
    """Per-test connection in a transaction that always rolls back (isolation).

    Depends on `db_settings` so the schema migration is guaranteed to have run
    before any test writes — every DB-touching fixture (incl. `seed_readings`)
    funnels through here, so tests never need to remember to request `db_settings`.
    """
    async with db_engine.connect() as conn:
        trans = await conn.begin()
        try:
            yield conn
        finally:
            await trans.rollback()


@pytest_asyncio.fixture
async def client(
    db_settings: dict[str, str], db_engine: AsyncEngine
) -> AsyncIterator[AsyncClient]:
    """An httpx client wired to the FastAPI app, with `get_session` overridden to
    use the testcontainers DB. The canonical API-test entry point for later steps.

    NOTE: requests commit through real sessions (no rollback isolation like
    `db_conn`). Tests that write should use distinct ids or truncate between runs.
    """
    from app.db.session import get_session
    from app.main import create_app

    maker = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

    async def _override_get_session() -> AsyncIterator[AsyncSession]:
        async with maker() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_session] = _override_get_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client


@pytest_asyncio.fixture
async def seed_readings(
    db_conn: AsyncConnection,
) -> Callable[..., Awaitable[int]]:
    """Insert N telemetry readings for a vehicle directly (no simulator).

    Upserts the vehicle by `external_id`, then writes `count` readings spaced
    `interval_seconds` apart starting at `start_ts`. Per-row metric values come
    from `**metrics` (constant across rows; default None). Returns the internal
    `vehicle.id`. Writes go through the test's rollback-isolated `db_conn`.

    Tests self-seed via this — never via the simulator (Step 5 is demos/load only).
    """

    async def _seed(
        vehicle_external_id: str,
        count: int = 1,
        *,
        start_ts: datetime | None = None,
        interval_seconds: float = 1.0,
        **metrics: float | None,
    ) -> int:
        vehicle_id: int = (
            await db_conn.execute(
                text(
                    "INSERT INTO vehicle (external_id, name) VALUES (:eid, :name) "
                    "ON CONFLICT (external_id) DO UPDATE SET name = EXCLUDED.name "
                    "RETURNING id"
                ),
                {"eid": vehicle_external_id, "name": vehicle_external_id},
            )
        ).scalar_one()

        base = start_ts or datetime(2026, 1, 1, tzinfo=UTC)
        cols = ("lat", "lon", "speed_kph", "soc_pct", "motor_temp_c", "odometer_km")
        rows = [
            {
                "vid": vehicle_id,
                "ts": base + timedelta(seconds=i * interval_seconds),
                **{c: metrics.get(c) for c in cols},
            }
            for i in range(count)
        ]
        if rows:
            await db_conn.execute(
                text(
                    "INSERT INTO telemetry_reading (vehicle_id, ts, lat, lon, "
                    "speed_kph, soc_pct, motor_temp_c, odometer_km) "
                    "VALUES (:vid, :ts, :lat, :lon, :speed_kph, :soc_pct, "
                    ":motor_temp_c, :odometer_km)"
                ),
                rows,
            )
        return vehicle_id

    return _seed
