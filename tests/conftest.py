import asyncio
import os
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any

# Must be set before app settings are imported. In docker the `tests` service overrides it.
os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5441/tasks_test"
)
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-at-least-32-bytes-long")

import pytest
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.db.session import get_session
from app.main import app

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PASSWORD = "Str0ngPassw0rd!"


def _migrate() -> None:
    # Real migrations instead of create_all: tests also cover upgrade/downgrade.
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")


@pytest.fixture(scope="session")
async def engine() -> AsyncIterator[AsyncEngine]:
    await asyncio.to_thread(_migrate)
    eng = create_async_engine(settings.database_url, poolclass=NullPool)
    yield eng
    await eng.dispose()


@pytest.fixture
async def session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """Each test runs in an outer transaction that is rolled back; service commits -> savepoints."""
    async with engine.connect() as conn:
        trans = await conn.begin()
        db = AsyncSession(
            bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False
        )
        try:
            yield db
        finally:
            await db.close()
            await trans.rollback()


@pytest.fixture
async def client(session: AsyncSession) -> AsyncIterator[AsyncClient]:
    async def override_session() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_session] = override_session
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


UserFactory = Callable[..., Awaitable[dict[str, Any]]]


@pytest.fixture
def create_user(client: AsyncClient) -> UserFactory:
    """Registers a user, logs in; returns user json + `headers` with bearer token."""

    async def factory(email: str | None = None, password: str = DEFAULT_PASSWORD) -> dict[str, Any]:
        email = email or f"user-{uuid.uuid4().hex[:8]}@example.com"
        resp = await client.post(
            "/api/v1/auth/register",
            json={"email": email, "password": password, "full_name": "Test User"},
        )
        assert resp.status_code == 201, resp.text
        user = resp.json()

        resp = await client.post(
            "/api/v1/auth/login", data={"username": email, "password": password}
        )
        assert resp.status_code == 200, resp.text
        tokens = resp.json()
        return {
            **user,
            "tokens": tokens,
            "headers": {"Authorization": f"Bearer {tokens['access_token']}"},
        }

    return factory
