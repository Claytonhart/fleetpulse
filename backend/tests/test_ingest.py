"""Step 4 — ingest endpoint: happy path, idempotency, validation, back-pressure.

End-to-end via the `client` fixture (committing path) + `clean_db` for isolation;
read-backs use `db_conn` (sees committed rows).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

CreateVehicle = Callable[..., Awaitable[int]]


def _reading(external_id: str, ts: str, **metrics: Any) -> dict[str, Any]:
    return {"vehicle_external_id": external_id, "ts": ts, **metrics}


async def _count_readings(conn: AsyncConnection) -> int:
    return int((await conn.execute(text("SELECT count(*) FROM telemetry_reading"))).scalar_one())


async def test_ingest_happy_path(
    client: AsyncClient,
    clean_db: None,
    create_vehicle: CreateVehicle,
    db_conn: AsyncConnection,
) -> None:
    await create_vehicle("VH-1")
    body = {
        "readings": [
            _reading("VH-1", "2026-06-03T12:00:00Z", motor_temp_c=80.0),
            _reading("VH-1", "2026-06-03T12:00:01Z", motor_temp_c=81.0),
        ]
    }
    resp = await client.post("/api/v1/telemetry", json=body)
    assert resp.status_code == 202
    assert resp.json() == {"inserted_count": 2, "duplicate_count": 0}
    assert await _count_readings(db_conn) == 2


async def test_ingest_idempotent_duplicate_batch(
    client: AsyncClient,
    clean_db: None,
    create_vehicle: CreateVehicle,
    db_conn: AsyncConnection,
) -> None:
    await create_vehicle("VH-1")
    body = {
        "readings": [
            _reading("VH-1", "2026-06-03T12:00:00Z"),
            _reading("VH-1", "2026-06-03T12:00:01Z"),
        ]
    }
    first = await client.post("/api/v1/telemetry", json=body)
    assert first.json() == {"inserted_count": 2, "duplicate_count": 0}

    second = await client.post("/api/v1/telemetry", json=body)
    assert second.status_code == 202
    assert second.json() == {"inserted_count": 0, "duplicate_count": 2}

    # No double-write: row count unchanged after the retry.
    assert await _count_readings(db_conn) == 2


async def test_ingest_unknown_vehicle_rejects_whole_batch(
    client: AsyncClient,
    clean_db: None,
    create_vehicle: CreateVehicle,
    db_conn: AsyncConnection,
) -> None:
    await create_vehicle("VH-1")
    body = {
        "readings": [
            _reading("VH-1", "2026-06-03T12:00:00Z"),  # valid
            _reading("VH-UNKNOWN", "2026-06-03T12:00:00Z"),  # unknown -> reject all
        ]
    }
    resp = await client.post("/api/v1/telemetry", json=body)
    assert resp.status_code == 422
    assert await _count_readings(db_conn) == 0  # nothing written


async def test_ingest_malformed_reading_rejects_whole_batch(
    client: AsyncClient,
    clean_db: None,
    create_vehicle: CreateVehicle,
    db_conn: AsyncConnection,
) -> None:
    await create_vehicle("VH-1")
    body = {
        "readings": [
            _reading("VH-1", "2026-06-03T12:00:00Z"),  # valid
            _reading("VH-1", "2026-06-03T12:00:01Z", speed_kph="not-a-number"),  # bad type
        ]
    }
    resp = await client.post("/api/v1/telemetry", json=body)
    assert resp.status_code == 422
    assert await _count_readings(db_conn) == 0


async def test_ingest_oversized_batch_rejected(
    client: AsyncClient,
    clean_db: None,
    create_vehicle: CreateVehicle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "max_batch_readings", 2)
    await create_vehicle("VH-1")
    body = {
        "readings": [_reading("VH-1", f"2026-06-03T12:00:0{i}Z") for i in range(3)]
    }
    resp = await client.post("/api/v1/telemetry", json=body)
    assert resp.status_code == 422


async def test_ingest_clears_offline_and_touches_last_seen(
    client: AsyncClient,
    clean_db: None,
    create_vehicle: CreateVehicle,
    db_conn: AsyncConnection,
) -> None:
    await create_vehicle("VH-OFF", status="offline")
    body = {"readings": [_reading("VH-OFF", "2026-06-03T12:00:00Z", motor_temp_c=50.0)]}
    resp = await client.post("/api/v1/telemetry", json=body)
    assert resp.status_code == 202

    row = (
        await db_conn.execute(
            text("SELECT status, last_seen_at FROM vehicle WHERE external_id = 'VH-OFF'")
        )
    ).one()
    assert row.status == "active"  # offline -> active on telemetry arrival (§10.13)
    assert row.last_seen_at is not None


async def test_ingest_duplicate_retry_does_not_resurrect_offline(
    client: AsyncClient,
    clean_db: None,
    create_vehicle: CreateVehicle,
    db_engine: AsyncEngine,
    db_conn: AsyncConnection,
) -> None:
    """A no-op duplicate retry (0 rows inserted) must NOT flip an offline vehicle
    back to active — durable status keys off ACTUALLY-inserted rows only (§10.13)."""
    await create_vehicle("VH-OFF", status="active")
    body = {"readings": [_reading("VH-OFF", "2026-06-03T12:00:00Z", motor_temp_c=50.0)]}

    # First ingest writes the row (and would set active).
    first = await client.post("/api/v1/telemetry", json=body)
    assert first.json() == {"inserted_count": 1, "duplicate_count": 0}

    # Now the vehicle goes offline (committed via a separate connection so the API
    # session sees it); retry the SAME batch -> 0 inserted.
    async with db_engine.begin() as conn:
        await conn.execute(
            text("UPDATE vehicle SET status = 'offline' WHERE external_id = 'VH-OFF'")
        )

    retry = await client.post("/api/v1/telemetry", json=body)
    assert retry.json() == {"inserted_count": 0, "duplicate_count": 1}

    status = (
        await db_conn.execute(
            text("SELECT status FROM vehicle WHERE external_id = 'VH-OFF'")
        )
    ).scalar_one()
    assert status == "offline"  # stayed offline — no resurrection from a no-op retry
