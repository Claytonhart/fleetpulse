"""Declarative base for all ORM models.

Kept import-light (no engine, no settings) so models and Alembic can import the
metadata without pulling in a live engine.
"""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Shared declarative base; `Base.metadata` is the migration target."""
