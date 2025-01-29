from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool
import os

# Use environment variables for configuration.
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://orderbook_user:orderbook_password@postgres:5432/orderbook_db",
)

# Create engine with connection pooling disabled for more predictable
# connection management.
engine = create_engine(
    DATABASE_URL,
    poolclass=NullPool,  # Disable connection pooling
    pool_pre_ping=True,  # Test connections before using them
)

# Create a sessionmaker for database interactions.
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    """
    Dependency that creates a new database session for each request.
    Yields the session and ensures it's closed after use.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
