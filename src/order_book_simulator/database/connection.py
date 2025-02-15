import os

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Use environment variables for configuration.
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://orderbook_user:orderbook_password@postgres:5432/orderbook_db",
)

# Create engine with connection pooling disabled for more predictable
# connection management.
engine = create_async_engine(
    DATABASE_URL,
    pool_size=20,  # Larger connection pool
    max_overflow=30,  # Allow more temporary connections
    pool_timeout=30,  # Longer timeout
    pool_pre_ping=True,  # Check connection health
    echo=False,  # Disable SQL logging for performance
)

# Create an async session factory for database interactions.
AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db():
    """
    Dependency that creates a new database session for each request.
    Yields the session and ensures it's closed after use.
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()
