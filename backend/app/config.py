"""Application configuration via pydantic-settings (12-factor).

Config is read from **OS environment variables** (which Docker Compose injects
into each container from `.env`). The app deliberately does NOT load `.env`
itself: inside Compose the DB host is the service name `timescaledb`, but a
host-side run (alembic, pytest, a local uvicorn) must reach the same DB at
`localhost`. Letting the app read `.env` would drag the Compose-internal hostname
onto the host and break those runs. So:

  * In Compose: `DATABASE_URL=...@timescaledb:5432/...` is injected as an env var.
  * On the host: nothing is set, and the localhost defaults below just work.
  * In tests: the testcontainers fixture supplies its own ephemeral URL.

Later steps extend this object (Redis URL, CORS, batch caps, etc.).
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(case_sensitive=False, extra="ignore")

    # Async SQLAlchemy/asyncpg URL. Compose overrides the host with `timescaledb`.
    database_url: str = (
        "postgresql+asyncpg://fleetpulse:fleetpulse@localhost:5432/fleetpulse"
    )

    # Connection pool sizing. `db_pool_acquire_timeout_seconds` is the bound that
    # Step 4 turns into a 503 when the pool is exhausted (back-pressure, §10.9).
    db_pool_size: int = 20
    db_max_overflow: int = 10
    db_pool_acquire_timeout_seconds: int = 5


settings = Settings()
