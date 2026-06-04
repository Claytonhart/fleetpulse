"""Shared FastAPI dependencies.

The `Annotated[..., Depends(...)]` alias form is used (not `Depends()` in a default
arg) so signatures stay clean and ruff's B008 never fires.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session

SessionDep = Annotated[AsyncSession, Depends(get_session)]
