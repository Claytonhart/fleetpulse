"""ORM models. Importing this package registers every table on `Base.metadata`
(what Alembic's `target_metadata` points at)."""

from __future__ import annotations

from app.db.base import Base
from app.models.alert_rule import AlertRule
from app.models.incident import Incident
from app.models.telemetry import TelemetryReading
from app.models.vehicle import Vehicle

__all__ = ["AlertRule", "Base", "Incident", "TelemetryReading", "Vehicle"]
