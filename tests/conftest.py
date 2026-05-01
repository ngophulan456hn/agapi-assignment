"""
conftest.py — shared pytest fixtures for all service tests.

All tests run against an in-process SQLite (async) database so no running
PostgreSQL is required.  Redis is replaced by a fully-featured fake provided
by the `fakeredis` library.

Fixtures:
  db       — AsyncSession scoped to each test (rolled back after).
  redis    — fakeredis async client scoped to each test.
  user     — a persisted, active User row (email-based).
  admin    — a persisted, active admin User row.
  product  — a persisted Product row.
"""

import asyncio
from decimal import Decimal
from typing import AsyncGenerator

import pytest
import pytest_asyncio
import fakeredis.aioredis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.security import hash_password
from app.models.base import Base
from app.models.flash_sale import (
    FlashSale,
    FlashSalePurchase,
)  # noqa: F401 — register models
from app.models.product import Product
from app.models.user import User


# ---------------------------------------------------------------------------
# Event loop (one loop per test session)
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


# ---------------------------------------------------------------------------
# In-memory async SQLite database
# ---------------------------------------------------------------------------
DATABASE_URL = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture(scope="session")
async def engine():
    eng = create_async_engine(DATABASE_URL, echo=False)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def db(engine) -> AsyncGenerator[AsyncSession, None]:
    """Fresh session per test; all changes are rolled back."""
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        async with session.begin():
            yield session
            await session.rollback()


# ---------------------------------------------------------------------------
# Fake Redis
# ---------------------------------------------------------------------------
@pytest_asyncio.fixture
async def redis():
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield client
    await client.aclose()


# ---------------------------------------------------------------------------
# Reusable model fixtures
# ---------------------------------------------------------------------------
@pytest_asyncio.fixture
async def user(db: AsyncSession) -> User:
    u = User(
        email="test@example.com",
        username="testuser",
        hashed_password=hash_password("Password1!"),
        is_active=True,
        is_admin=False,
        balance=Decimal("100.00"),
    )
    db.add(u)
    await db.flush()
    await db.refresh(u)
    return u


@pytest_asyncio.fixture
async def admin(db: AsyncSession) -> User:
    u = User(
        email="admin@example.com",
        username="adminuser",
        hashed_password=hash_password("Admin@1234!"),
        is_active=True,
        is_admin=True,
        balance=Decimal("0.00"),
    )
    db.add(u)
    await db.flush()
    await db.refresh(u)
    return u


@pytest_asyncio.fixture
async def product(db: AsyncSession) -> Product:
    p = Product(
        name="Test Widget",
        description="A widget for testing",
        price=Decimal("29.99"),
        stock=100,
    )
    db.add(p)
    await db.flush()
    await db.refresh(p)
    return p
