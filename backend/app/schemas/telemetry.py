"""Ingest request/response schemas (SPEC §6).

Whole-batch validation: Pydantic validates the entire body before the endpoint
runs, so any malformed reading rejects the whole batch with 422 and nothing is
written (§10.8). Producers reference vehicles by `vehicle_external_id` (§10.11).
"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, Field, field_validator


class TelemetryReadingIn(BaseModel):
    vehicle_external_id: str
    ts: datetime
    lat: float | None = None
    lon: float | None = None
    speed_kph: float | None = None
    soc_pct: float | None = None
    motor_temp_c: float | None = None
    odometer_km: float | None = None
    error_codes: list[str] = Field(default_factory=list)

    @field_validator("ts")
    @classmethod
    def _ensure_tz_aware(cls, value: datetime) -> datetime:
        # Normalize naive timestamps to UTC so the timestamptz column is unambiguous.
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


class TelemetryBatchIn(BaseModel):
    readings: list[TelemetryReadingIn]


class TelemetryIngestResponse(BaseModel):
    inserted_count: int
    duplicate_count: int
