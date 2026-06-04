"""Enumerations and their Postgres native-enum type bindings.

The Python `Enum`s are the values the application code uses; the `*_pg` objects
are the SQLAlchemy column types that map to native Postgres enum types. They use
`create_type=False` because the enum types are created explicitly (once each) in
the Alembic migration — this avoids SQLAlchemy trying to `CREATE TYPE severity`
twice (it's shared by `alert_rule` and `incident`). `values_callable` makes the DB
store each member's `.value` (e.g. operator `>`), not its `.name` (`gt`).
"""

from __future__ import annotations

import enum

from sqlalchemy.dialects.postgresql import ENUM as PgEnum


class VehicleStatus(str, enum.Enum):
    active = "active"
    idle = "idle"
    offline = "offline"
    maintenance = "maintenance"


class RuleOperator(str, enum.Enum):
    gt = ">"
    gte = ">="
    lt = "<"
    lte = "<="
    eq = "=="


class Severity(str, enum.Enum):
    info = "info"
    warning = "warning"
    critical = "critical"


class IncidentStatus(str, enum.Enum):
    open = "open"
    acknowledged = "acknowledged"
    resolved = "resolved"


def _pg_enum(py_enum: type[enum.Enum], name: str) -> PgEnum:
    """A native-Postgres enum column type that references an existing DB type."""
    return PgEnum(
        py_enum,
        name=name,
        create_type=False,
        values_callable=lambda enum_cls: [member.value for member in enum_cls],
    )


vehicle_status_pg = _pg_enum(VehicleStatus, "vehicle_status")
rule_operator_pg = _pg_enum(RuleOperator, "rule_operator")
severity_pg = _pg_enum(Severity, "severity")
incident_status_pg = _pg_enum(IncidentStatus, "incident_status")
