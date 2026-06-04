"""Step 3 — the health endpoint is green against the (testcontainers) DB."""

from __future__ import annotations

from httpx import AsyncClient


async def test_health_ok(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "db": "ok"}
