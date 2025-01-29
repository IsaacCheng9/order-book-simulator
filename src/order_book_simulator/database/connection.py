from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.pool import NullPool
import os

# Use environment variables for configuration.
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://orderbook_user:orderbook_password@postgres:5432/orderbook_db",
)

# Create engine with connection pooling disabled for more predictable
# connection management.
engine = create_async_engine(
    DATABASE_URL,
    poolclass=NullPool,  # Disable connection pooling
    pool_pre_ping=True,  # Test connections before using them
)

# Create an async session factory for database interactions.
AsyncSessionLocal = async_sessionmaker(
    bind=engine, class_=AsyncSession, expire_on_commit=False
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
