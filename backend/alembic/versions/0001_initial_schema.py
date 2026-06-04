"""initial schema: enums, tables, hypertable, indexes

Revision ID: 0001
Revises:
Create Date: 2026-06-03

Order (per IMPLEMENTATION_PLAN Step 2):
  1. CREATE EXTENSION timescaledb
  2. create native enum types, then tables (telemetry_reading has composite PK)
  3. create_hypertable('telemetry_reading', 'ts')  -- works: PK includes ts
  4. indexes incl. the partial-unique active-incident guard

All DDL here is transaction-safe (the autocommit-block requirement is Step 7 only,
for continuous aggregates / policies).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")

    # Native enum types — created once here; models bind them with create_type=False.
    op.execute("CREATE TYPE vehicle_status AS ENUM ('active','idle','offline','maintenance')")
    op.execute("CREATE TYPE rule_operator AS ENUM ('>','>=','<','<=','==')")
    op.execute("CREATE TYPE severity AS ENUM ('info','warning','critical')")
    op.execute("CREATE TYPE incident_status AS ENUM ('open','acknowledged','resolved')")

    vehicle_status = postgresql.ENUM(name="vehicle_status", create_type=False)
    rule_operator = postgresql.ENUM(name="rule_operator", create_type=False)
    severity = postgresql.ENUM(name="severity", create_type=False)
    incident_status = postgresql.ENUM(name="incident_status", create_type=False)

    op.create_table(
        "vehicle",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("external_id", sa.String(64), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("model", sa.String(128), nullable=True),
        sa.Column("status", vehicle_status, nullable=False, server_default=sa.text("'active'")),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("external_id", name="uq_vehicle_external_id"),
    )

    op.create_table(
        "alert_rule",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("metric", sa.String(64), nullable=False),
        sa.Column("operator", rule_operator, nullable=False),
        sa.Column("threshold", sa.Float(), nullable=False),
        sa.Column("recovery_threshold", sa.Float(), nullable=False),
        sa.Column("severity", severity, nullable=False),
        sa.Column("window_count", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
    )

    # telemetry_reading: NO surrogate id; composite PK (vehicle_id, ts).
    op.create_table(
        "telemetry_reading",
        sa.Column(
            "vehicle_id",
            sa.BigInteger(),
            sa.ForeignKey("vehicle.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lat", sa.Float(), nullable=True),
        sa.Column("lon", sa.Float(), nullable=True),
        sa.Column("speed_kph", sa.Float(), nullable=True),
        sa.Column("soc_pct", sa.Float(), nullable=True),
        sa.Column("motor_temp_c", sa.Float(), nullable=True),
        sa.Column("odometer_km", sa.Float(), nullable=True),
        sa.Column("error_codes", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.PrimaryKeyConstraint("vehicle_id", "ts", name="pk_telemetry_reading"),
    )

    # Turn it into a hypertable (PK includes the partition column ts, so this works).
    op.execute("SELECT create_hypertable('telemetry_reading', 'ts', if_not_exists => TRUE)")

    op.create_table(
        "incident",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "vehicle_id",
            sa.BigInteger(),
            sa.ForeignKey("vehicle.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "rule_id",
            sa.Integer(),
            sa.ForeignKey("alert_rule.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("severity", severity, nullable=False),
        sa.Column("status", incident_status, nullable=False, server_default=sa.text("'open'")),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("opened_value", sa.Float(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
    )

    # Indexes (SPEC §5). DESC and partial indexes go via raw SQL.
    op.execute(
        "CREATE INDEX ix_telemetry_reading_vehicle_ts "
        "ON telemetry_reading (vehicle_id, ts DESC)"
    )
    op.execute("CREATE INDEX ix_alert_rule_enabled ON alert_rule (id) WHERE enabled")
    op.create_index(
        "ix_incident_status_severity_vehicle",
        "incident",
        ["status", "severity", "vehicle_id"],
    )
    # At most one active incident per (vehicle, rule) — the double-open guard.
    op.execute(
        "CREATE UNIQUE INDEX uq_incident_active "
        "ON incident (vehicle_id, rule_id) WHERE status != 'resolved'"
    )


def downgrade() -> None:
    # Dropping the tables also drops their indexes (incl. the raw-SQL ones).
    # Order reverses the FK dependencies; enum types are dropped last.
    op.drop_table("incident")
    op.drop_table("telemetry_reading")
    op.drop_table("alert_rule")
    op.drop_table("vehicle")
    op.execute("DROP TYPE IF EXISTS incident_status")
    op.execute("DROP TYPE IF EXISTS rule_operator")
    op.execute("DROP TYPE IF EXISTS vehicle_status")
    op.execute("DROP TYPE IF EXISTS severity")
