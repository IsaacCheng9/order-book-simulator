import testing.postgresql
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

# Production database URL
DATABASE_URL = "postgresql+asyncpg://postgres:postgres@postgres:5432/postgres"

# Create async engine
engine = create_async_engine(DATABASE_URL)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

# Create temporary test database
Postgresql = testing.postgresql.PostgresqlFactory(cache_initialized_db=True)
postgresql = Postgresql()
TEST_DATABASE_URL = postgresql.url().replace("postgresql://", "postgresql+asyncpg://")
test_engine = create_async_engine(TEST_DATABASE_URL)
TestingSessionLocal = sessionmaker(
    test_engine, class_=AsyncSession, expire_on_commit=False
)


async def get_db() -> AsyncSession:
    """Dependency for getting database sessions."""
    async with AsyncSessionLocal() as session:
        yield session
