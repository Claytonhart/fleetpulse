"""Alembic environment — async (asyncpg) migrations.

DB URL precedence (so the same config works from CLI, tests, and Compose):
  1. `-x db_url=...`  (CLI override, e.g. host runs against localhost)
  2. `ALEMBIC_DB_URL` env var  (used by the pytest conftest fixture)
  3. `settings.database_url`  (the app default / Compose-injected value)
"""

from __future__ import annotations

import asyncio
import os
import pathlib
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

# Make the backend/ package root importable regardless of CWD.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402
from app.models import Base  # noqa: E402

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _resolve_url() -> str:
    x_args = context.get_x_argument(as_dictionary=True)
    return (
        x_args.get("db_url")
        or os.environ.get("ALEMBIC_DB_URL")
        or settings.database_url
    )


def run_migrations_offline() -> None:
    context.configure(
        url=_resolve_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = create_async_engine(_resolve_url(), poolclass=pool.NullPool)
    async with engine.connect() as connection:
        await connection.run_sync(_do_run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
