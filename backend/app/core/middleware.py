"""Request logging + correlation IDs.

Pure ASGI rather than BaseHTTPMiddleware: the request ID must stay bound
for the whole response — including streamed SSE bodies and the final
"<--" log line — and an unhandled error must become the 500 envelope while
the ID is still known (Starlette's outermost error middleware runs after
any user middleware has already unwound).
"""

import time

from starlette.datastructures import Headers, MutableHeaders
from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.errors import unhandled_error_handler
from app.core.logging import clear_request_id, get_logger, set_request_id

logger = get_logger(__name__)

REQUEST_ID_HEADER = "X-Request-ID"


class RequestLoggingMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        rid = set_request_id(Headers(scope=scope).get(REQUEST_ID_HEADER))
        path = scope["path"]
        if scope["type"] == "websocket":
            # Sockets get an ID too, so a voice session's logs correlate.
            logger.info("--> WS %s", path)
            try:
                await self.app(scope, receive, send)
            finally:
                logger.info("<-- WS %s closed", path)
                clear_request_id()
            return

        method = scope["method"]
        start = time.perf_counter()
        status = 500
        started = False

        async def send_with_id(message: Message) -> None:
            nonlocal status, started
            if message["type"] == "http.response.start":
                started = True
                status = message["status"]
                MutableHeaders(scope=message).append(REQUEST_ID_HEADER, rid)
            await send(message)

        logger.info("--> %s %s", method, path)
        try:
            await self.app(scope, receive, send_with_id)
        except Exception as exc:
            if started:
                # Headers are already out — nothing left to send but the
                # truncated body; let the server close the connection.
                logger.exception("Unhandled error mid-response on %s %s", method, path)
                raise
            response = await unhandled_error_handler(Request(scope), exc)
            await response(scope, receive, send_with_id)
        finally:
            elapsed_ms = (time.perf_counter() - start) * 1000
            logger.info("<-- %s %s -> %d (%.1fms)", method, path, status, elapsed_ms)
            clear_request_id()
