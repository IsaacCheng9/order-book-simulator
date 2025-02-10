"""Database session management."""

import os
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Use test database URL in test environment
DATABASE_URL = (
    "postgresql+asyncpg://test:test@test:5432/test"
    if os.getenv("TESTING")
    else "postgresql+asyncpg://postgres:postgres@postgres:5432/postgres"
)

# Create async engine
engine = create_async_engine(DATABASE_URL)
AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

# Test engine and session
test_engine = create_async_engine("postgresql+asyncpg://test:test@test:5432/test")
TestingSessionLocal = async_sessionmaker(
    test_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    Dependency for getting database sessions.

    Yields:
        AsyncSession: A database session for handling requests.
    """
    async with AsyncSessionLocal() as session:
        yield session
