"""`alert_rule` — data-driven rules (SPEC §5, §4.3). No `window_seconds` in v1."""

from __future__ import annotations

from sqlalchemy import Boolean, Float, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.enums import RuleOperator, Severity, rule_operator_pg, severity_pg


class AlertRule(Base):
    __tablename__ = "alert_rule"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    # Scalar metric name only in v1 (e.g. "motor_temp_c"); error_codes is Phase 2 (§10.12).
    metric: Mapped[str] = mapped_column(String(64), nullable=False)
    operator: Mapped[RuleOperator] = mapped_column(rule_operator_pg, nullable=False)
    threshold: Mapped[float] = mapped_column(Float, nullable=False)
    # Separate recovery threshold creates the hysteresis dead band (§10.4).
    recovery_threshold: Mapped[float] = mapped_column(Float, nullable=False)
    severity: Mapped[Severity] = mapped_column(severity_pg, nullable=False)
    window_count: Mapped[int] = mapped_column(Integer, nullable=False)
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
