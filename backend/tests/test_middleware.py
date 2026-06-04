"""Step 4 — body-size cap middleware (§10.9 → 413). Tested in isolation so we don't
ship a 1 MB payload: both the Content-Length fast path and the chunked buffering path."""

from __future__ import annotations

from collections.abc import AsyncIterator

from httpx import ASGITransport, AsyncClient
from starlette.responses import PlainTextResponse
from starlette.types import Receive, Scope, Send

from app.middleware import BodySizeLimitMiddleware


async def _ok_app(scope: Scope, receive: Receive, send: Send) -> None:
    while True:
        message = await receive()
        if not message.get("more_body", False):
            break
    await PlainTextResponse("ok", status_code=200)(scope, receive, send)


def _client() -> AsyncClient:
    app = BodySizeLimitMiddleware(_ok_app, max_body_bytes=10)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_under_cap_passes_through() -> None:
    async with _client() as c:
        resp = await c.post("/", content=b"x" * 5)
        assert resp.status_code == 200


async def test_over_cap_content_length_rejected() -> None:
    async with _client() as c:
        resp = await c.post("/", content=b"x" * 50)  # httpx sets Content-Length
        assert resp.status_code == 413
        # JSON {"detail": ...}, consistent with the 422/503 error contract.
        assert resp.json()["detail"] == "request body too large"


async def test_over_cap_chunked_rejected() -> None:
    async def _chunks() -> AsyncIterator[bytes]:
        yield b"x" * 50  # no Content-Length -> exercises the buffering path

    async with _client() as c:
        resp = await c.post("/", content=_chunks())
        assert resp.status_code == 413
