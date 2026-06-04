"""`incident` — durable incident records (SPEC §5).

A partial unique index `UNIQUE (vehicle_id, rule_id) WHERE status != 'resolved'`
(created in the migration) guarantees at most one active incident per
(vehicle, rule), so concurrent workers can't double-open.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Float, ForeignKey, Integer, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.enums import (
    IncidentStatus,
    Severity,
    incident_status_pg,
    severity_pg,
)


class Incident(Base):
    __tablename__ = "incident"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    vehicle_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("vehicle.id", ondelete="CASCADE"), nullable=False
    )
    rule_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("alert_rule.id", ondelete="CASCADE"), nullable=False
    )
    severity: Mapped[Severity] = mapped_column(severity_pg, nullable=False)
    status: Mapped[IncidentStatus] = mapped_column(
        incident_status_pg, nullable=False, server_default=text("'open'")
    )
    opened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    acknowledged_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # The reading value that tripped the rule.
    opened_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
