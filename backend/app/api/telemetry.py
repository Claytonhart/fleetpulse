"""`POST /telemetry` — batched, validated, idempotent, back-pressured ingest."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.api.deps import SessionDep
from app.config import settings
from app.schemas.telemetry import TelemetryBatchIn, TelemetryIngestResponse
from app.services.ingest import UnknownVehicleError, ingest_batch

router = APIRouter(tags=["telemetry"])


@router.post("/telemetry", status_code=202, response_model=TelemetryIngestResponse)
async def ingest_telemetry(
    batch: TelemetryBatchIn, session: SessionDep
) -> TelemetryIngestResponse:
    # Batch-size cap (§10.9). Body-size cap is enforced earlier by ASGI middleware;
    # pool-exhaustion → 503 is handled globally in the app factory.
    if len(batch.readings) > settings.max_batch_readings:
        raise HTTPException(
            status_code=422,
            detail=f"batch exceeds max of {settings.max_batch_readings} readings",
        )
    try:
        result = await ingest_batch(session, batch)
    except UnknownVehicleError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return TelemetryIngestResponse(
        inserted_count=result.inserted_count,
        duplicate_count=result.duplicate_count,
    )
