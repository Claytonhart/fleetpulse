"""ASGI middleware.

`BodySizeLimitMiddleware` enforces the request-body cap (§10.9) → 413. Starlette has
no built-in max-body, so this is real ASGI middleware, not a config flag:
  * If a valid `Content-Length` is present, trust it (fast path, no buffering).
  * Otherwise (chunked / no length) buffer up to the cap, reject if exceeded, then
    replay the buffered body to the app.
"""

from __future__ import annotations

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class BodySizeLimitMiddleware:
    def __init__(self, app: ASGIApp, max_body_bytes: int) -> None:
        self.app = app
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        content_length = headers.get(b"content-length")
        if content_length is not None:
            try:
                declared = int(content_length.decode("latin-1"))
            except ValueError:
                declared = None
            if declared is not None:
                if declared > self.max_body_bytes:
                    await self._reject(scope, receive, send)
                    return
                await self.app(scope, receive, send)
                return

        # No (valid) Content-Length: buffer with a cap, then replay.
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.request":
                body.extend(message.get("body", b""))
                if len(body) > self.max_body_bytes:
                    await self._reject(scope, receive, send)
                    return
                if not message.get("more_body", False):
                    break
            elif message["type"] == "http.disconnect":
                break

        replayed = False

        async def replay() -> Message:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return {"type": "http.disconnect"}

        await self.app(scope, replay, send)

    async def _reject(self, scope: Scope, receive: Receive, send: Send) -> None:
        # JSON {"detail": ...} to match the 422/503 error contract (not plain text).
        response = JSONResponse({"detail": "request body too large"}, status_code=413)
        await response(scope, receive, send)
