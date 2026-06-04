"""Step 2 schema tests — assert the migration produced the structures the spec
requires: the hypertable, the composite PK, and the partial-unique incident guard.

These depend on `db_settings` (which runs `alembic upgrade head`) and query the
live catalog through the rollback-isolated `db_conn`.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection


async def test_telemetry_reading_is_hypertable(
    db_settings: dict[str, str], db_conn: AsyncConnection
) -> None:
    result = await db_conn.execute(
        text(
            "SELECT hypertable_name FROM timescaledb_information.hypertables "
            "WHERE hypertable_name = 'telemetry_reading'"
        )
    )
    assert result.scalar_one() == "telemetry_reading"


async def test_telemetry_reading_composite_pk(
    db_settings: dict[str, str], db_conn: AsyncConnection
) -> None:
    """PK is exactly (vehicle_id, ts) — no surrogate id."""
    result = await db_conn.execute(
        text(
            "SELECT a.attname "
            "FROM pg_index i "
            "JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey) "
            "WHERE i.indrelid = 'telemetry_reading'::regclass AND i.indisprimary"
        )
    )
    cols = {row[0] for row in result.fetchall()}
    assert cols == {"vehicle_id", "ts"}


async def test_incident_partial_unique_index(
    db_settings: dict[str, str], db_conn: AsyncConnection
) -> None:
    """The active-incident guard exists, and is both UNIQUE and PARTIAL."""
    result = await db_conn.execute(
        text(
            "SELECT i.indisunique, pg_get_expr(i.indpred, i.indrelid) AS predicate "
            "FROM pg_index i "
            "WHERE i.indexrelid = 'uq_incident_active'::regclass"
        )
    )
    row = result.one()
    assert row.indisunique is True
    assert row.predicate is not None  # partial — has a WHERE clause
    assert "resolved" in row.predicate


async def test_enum_types_present(
    db_settings: dict[str, str], db_conn: AsyncConnection
) -> None:
    result = await db_conn.execute(
        text(
            "SELECT typname FROM pg_type "
            "WHERE typname IN ('vehicle_status','rule_operator','severity','incident_status')"
        )
    )
    names = {row[0] for row in result.fetchall()}
    assert names == {"vehicle_status", "rule_operator", "severity", "incident_status"}


async def test_insert_vehicle_and_reading(
    db_settings: dict[str, str], db_conn: AsyncConnection
) -> None:
    """Smoke-write through the FK + hypertable (rolled back by the fixture)."""
    vehicle_id = (
        await db_conn.execute(
            text(
                "INSERT INTO vehicle (external_id, name) "
                "VALUES ('VH-TEST', 'Test') RETURNING id"
            )
        )
    ).scalar_one()

    ts = datetime(2026, 6, 3, 12, 0, 0, tzinfo=UTC)
    await db_conn.execute(
        text(
            "INSERT INTO telemetry_reading (vehicle_id, ts, motor_temp_c) "
            "VALUES (:vid, :ts, 42.5)"
        ),
        {"vid": vehicle_id, "ts": ts},
    )

    count = (
        await db_conn.execute(
            text("SELECT count(*) FROM telemetry_reading WHERE vehicle_id = :vid"),
            {"vid": vehicle_id},
        )
    ).scalar_one()
    assert count == 1


async def test_seed_readings_helper(
    db_settings: dict[str, str],
    db_conn: AsyncConnection,
    seed_readings: Callable[..., Awaitable[int]],
) -> None:
    """The shared seeding helper writes N readings for a vehicle."""
    vehicle_id = await seed_readings("VH-SEED", 5, motor_temp_c=70.0)
    count = (
        await db_conn.execute(
            text("SELECT count(*) FROM telemetry_reading WHERE vehicle_id = :vid"),
            {"vid": vehicle_id},
        )
    ).scalar_one()
    assert count == 5
