"""Ingest service — validate → resolve → idempotent insert → durable status.

The request hot path does the minimum and returns fast (§4.1). Cache write,
telemetry publish, and per-vehicle rule enqueue are NOT done here yet — they hang
off the `_post_insert_side_effects` seam (Steps 6 & 9).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import case, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import VehicleStatus
from app.models.telemetry import TelemetryReading
from app.models.vehicle import Vehicle
from app.schemas.telemetry import TelemetryBatchIn


class UnknownVehicleError(Exception):
    """Raised when a batch references an unregistered external_id (→ 422, §10.11)."""

    def __init__(self, external_ids: Iterable[str]) -> None:
        self.external_ids = sorted(set(external_ids))
        super().__init__(f"unknown vehicle_external_id(s): {self.external_ids}")


@dataclass(frozen=True)
class EnrichedReading:
    """An actually-inserted reading + its `vehicle_external_id`.

    The DB row only carries the internal `vehicle_id`, but cache keys
    (`fleet:vehicle:{external_id}:state`) and all WS payloads are keyed by
    `external_id` — so the seam hands Steps 6/9 these enriched rows, not raw rows.
    """

    vehicle_id: int
    vehicle_external_id: str
    ts: datetime
    lat: float | None
    lon: float | None
    speed_kph: float | None
    soc_pct: float | None
    motor_temp_c: float | None
    odometer_km: float | None
    error_codes: list[str]


@dataclass
class IngestResult:
    inserted_count: int
    duplicate_count: int
    inserted_readings: list[EnrichedReading]


async def ingest_batch(session: AsyncSession, batch: TelemetryBatchIn) -> IngestResult:
    submitted = batch.readings
    if not submitted:
        return IngestResult(0, 0, [])

    # 1. Resolve external_id -> internal id in ONE bulk query (not per reading).
    external_ids = {r.vehicle_external_id for r in submitted}
    resolved = (
        await session.execute(
            select(Vehicle.id, Vehicle.external_id).where(
                Vehicle.external_id.in_(external_ids)
            )
        )
    ).all()
    id_by_external: dict[str, int] = {ext: vid for vid, ext in resolved}
    # id -> external_id, carried straight from the query (no redundant inversion).
    external_by_id: dict[int, str] = {vid: ext for vid, ext in resolved}

    # 2. Whole-batch reject on any unknown vehicle (§10.8, §10.11) — nothing written.
    unknown = external_ids - id_by_external.keys()
    if unknown:
        raise UnknownVehicleError(unknown)

    # 3. Build insert rows; de-dupe (vehicle_id, ts) within the batch.
    seen: set[tuple[int, datetime]] = set()
    insert_rows: list[dict[str, object]] = []
    for r in submitted:
        vid = id_by_external[r.vehicle_external_id]
        key = (vid, r.ts)
        if key in seen:
            continue
        seen.add(key)
        insert_rows.append(
            {
                "vehicle_id": vid,
                "ts": r.ts,
                "lat": r.lat,
                "lon": r.lon,
                "speed_kph": r.speed_kph,
                "soc_pct": r.soc_pct,
                "motor_temp_c": r.motor_temp_c,
                "odometer_km": r.odometer_km,
                "error_codes": r.error_codes,
            }
        )

    # 4. Idempotent batched insert (§10.1, §10.3). RETURNING = ACTUALLY-inserted rows,
    #    so a retried batch double-writes nothing and (via the seam) double-alerts nothing.
    stmt = (
        pg_insert(TelemetryReading)
        .values(insert_rows)
        .on_conflict_do_nothing(index_elements=["vehicle_id", "ts"])
        .returning(*TelemetryReading.__table__.columns)
    )
    inserted = (await session.execute(stmt)).mappings().all()

    # 5. Durable status (§10.13), keyed off ACTUALLY-inserted rows only: a no-op
    #    duplicate retry (0 rows inserted) must NOT touch last_seen_at or resurrect
    #    an offline vehicle. One UPDATE per distinct inserted vehicle.
    per_vehicle_max_ts: dict[int, datetime] = {}
    for row in inserted:
        vid_ins, ts_ins = row["vehicle_id"], row["ts"]
        cur = per_vehicle_max_ts.get(vid_ins)
        if cur is None or ts_ins > cur:
            per_vehicle_max_ts[vid_ins] = ts_ins
    for vid, max_ts in per_vehicle_max_ts.items():
        await session.execute(
            update(Vehicle)
            .where(Vehicle.id == vid)
            .values(
                last_seen_at=func.greatest(Vehicle.last_seen_at, max_ts),
                status=case(
                    (Vehicle.status == VehicleStatus.offline, VehicleStatus.active),
                    else_=Vehicle.status,
                ),
            )
        )

    await session.commit()

    enriched = [
        EnrichedReading(
            vehicle_id=row["vehicle_id"],
            vehicle_external_id=external_by_id[row["vehicle_id"]],
            ts=row["ts"],
            lat=row["lat"],
            lon=row["lon"],
            speed_kph=row["speed_kph"],
            soc_pct=row["soc_pct"],
            motor_temp_c=row["motor_temp_c"],
            odometer_km=row["odometer_km"],
            error_codes=row["error_codes"],
        )
        for row in inserted
    ]

    await _post_insert_side_effects(enriched)

    return IngestResult(
        inserted_count=len(enriched),
        duplicate_count=len(submitted) - len(enriched),
        inserted_readings=enriched,
    )


async def _post_insert_side_effects(readings: list[EnrichedReading]) -> None:
    """SEAM for Steps 6 & 9 — runs on the ACTUALLY-inserted (enriched) rows only.

    * Step 6: write Redis hot-state (`fleet:vehicle:{external_id}:state`) and PUBLISH
      a `telemetry_delta` to `fleet:telemetry`.
    * Step 9a: group by vehicle, sort by ts, enqueue ONE Celery task per vehicle.

    Known v1 tradeoff (post-DB side-effect failure window): if the insert commits but
    a side effect here fails and the producer retries the SAME batch, the re-insert
    returns no rows (ON CONFLICT) so these effects never run for them — at-least-once
    is not guaranteed end-to-end. Production fix = transactional outbox (README; not v1).
    """
    return
