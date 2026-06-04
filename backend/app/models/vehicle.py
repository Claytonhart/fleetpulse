"""`vehicle` — durable, low-cardinality fleet roster (SPEC §5)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.enums import VehicleStatus, vehicle_status_pg


class Vehicle(Base):
    __tablename__ = "vehicle"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    external_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Durable status: single writer = ingest touch + offline-detection beat (§10.13).
    status: Mapped[VehicleStatus] = mapped_column(
        vehicle_status_pg, nullable=False, server_default=text("'active'")
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
