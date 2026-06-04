"""`telemetry_reading` — the high-volume TimescaleDB hypertable (SPEC §5).

**NO surrogate `id`.** The primary key is the composite `(vehicle_id, ts)` — this
both enforces the `(vehicle_id, ts)` idempotency key (§10.3) and satisfies
TimescaleDB's rule that the partitioning column (`ts`) must be part of any PK/unique
constraint, so `create_hypertable` can succeed. Sensor metrics are nullable: real
telemetry has gaps, and continuous aggregates handle NULLs fine.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Float, ForeignKey, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class TelemetryReading(Base):
    __tablename__ = "telemetry_reading"

    vehicle_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("vehicle.id", ondelete="CASCADE"), primary_key=True
    )
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    speed_kph: Mapped[float | None] = mapped_column(Float, nullable=True)
    soc_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    motor_temp_c: Mapped[float | None] = mapped_column(Float, nullable=True)
    odometer_km: Mapped[float | None] = mapped_column(Float, nullable=True)
    error_codes: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
