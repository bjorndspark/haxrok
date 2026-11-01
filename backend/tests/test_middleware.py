"""RequestLoggingMiddleware: request IDs on every response — including the
500 envelope for an unhandled error — and on the response log line."""

import logging

from httpx import ASGITransport, AsyncClient

from app.core.logging import RequestContextFilter
from app.core.middleware import REQUEST_ID_HEADER, RequestLoggingMiddleware


async def _boom(scope, receive, send):
    raise RuntimeError("kaboom")


async def _ok(scope, receive, send):
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


def _client(app) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=RequestLoggingMiddleware(app)), base_url="http://t"
    )


async def test_unhandled_error_gets_envelope_with_request_id():
    async with _client(_boom) as c:
        resp = await c.get("/x", headers={REQUEST_ID_HEADER: "abc123"})

    assert resp.status_code == 500
    assert resp.headers[REQUEST_ID_HEADER] == "abc123"
    body = resp.json()
    assert body["success"] is False
    assert body["error"]["code"] == "INTERNAL_ERROR"
    assert body["request_id"] == "abc123"


async def test_response_log_line_carries_request_id(caplog):
    caplog.handler.addFilter(RequestContextFilter())
    with caplog.at_level(logging.INFO, logger="app.core.middleware"):
        async with _client(_ok) as c:
            resp = await c.get("/y", headers={REQUEST_ID_HEADER: "rid42"})

    assert resp.headers[REQUEST_ID_HEADER] == "rid42"
    done = [r for r in caplog.records if r.getMessage().startswith("<--")]
    assert done and done[0].request_id == "rid42"  # type: ignore[attr-defined]
