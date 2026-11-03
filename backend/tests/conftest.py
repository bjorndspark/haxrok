"""Test fixtures: an ephemeral SQLite DB (aiosqlite) replaces Postgres, and
the AI client is faked — no gateway, no network. Add feature fixtures here."""

from collections.abc import AsyncGenerator

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.db import Base, get_db
from app.main import app

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"

test_engine = create_async_engine(TEST_DB_URL)
TestSessionLocal = async_sessionmaker(test_engine, expire_on_commit=False)


@event.listens_for(test_engine.sync_engine, "connect")
def _enforce_foreign_keys(dbapi_conn, _record) -> None:
    # SQLite ignores FKs (and so ON DELETE CASCADE) unless asked — without
    # this, cascade behavior the app relies on in Postgres goes untested.
    dbapi_conn.execute("PRAGMA foreign_keys=ON")


@pytest_asyncio.fixture(scope="session", autouse=True)
async def create_schema() -> AsyncGenerator[None, None]:
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    await test_engine.dispose()


@pytest_asyncio.fixture
async def db() -> AsyncGenerator[AsyncSession, None]:
    async with TestSessionLocal() as session:
        yield session


@pytest_asyncio.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    """HTTP client with get_db overridden to the in-memory test DB."""

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with TestSessionLocal() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
