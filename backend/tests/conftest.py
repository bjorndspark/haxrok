"""Test fixtures: an in-process HTTP client against the app. The AI client
is faked per test — no gateway, no network. Add feature fixtures here."""

from collections.abc import AsyncGenerator

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest_asyncio.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    """HTTP client that calls the app in-process; dependency overrides set by
    a test are cleared afterwards."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
