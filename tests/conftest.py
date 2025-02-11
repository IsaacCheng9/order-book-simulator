from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.testclient import TestClient

from order_book_simulator.common.cache import order_book_cache
from order_book_simulator.database.models import Base
from order_book_simulator.database.session import get_db, test_engine
from order_book_simulator.gateway.app import app, app_state
from order_book_simulator.matching.engine import MatchingEngine
from order_book_simulator.matching.order_book import OrderBook


class MockMarketDataPublisher:
    def __init__(self):
        self.published_updates = []

    async def __call__(self, stock_id: UUID, market_data: dict) -> None:
        self.published_updates.append((stock_id, market_data))


@pytest.fixture(autouse=True)
def mock_kafka_producer():
    """Creates a mock Kafka producer for testing."""
    producer_mock = AsyncMock()
    producer_mock.start = AsyncMock()
    producer_mock.stop = AsyncMock()
    producer_mock.send_and_wait = AsyncMock()
    return producer_mock


@pytest.fixture
def market_data_publisher():
    """Creates a mock market data publisher for testing."""
    return MockMarketDataPublisher()


@pytest.fixture
def matching_engine(market_data_publisher) -> MatchingEngine:
    """Creates a matching engine instance for testing."""
    engine = MatchingEngine(market_data_publisher)
    app_state.matching_engine = engine
    return engine


class MockPostgres:
    """Mock PostgreSQL instance for testing."""

    def stop(self):
        pass

    def url(self):
        return "postgresql://mock:5432/mockdb"


@pytest.fixture(scope="session", autouse=True)
def postgresql_instance():
    """Creates a mock PostgreSQL instance for testing."""
    return MockPostgres()


@pytest.fixture
def db_session() -> AsyncSession:
    """Creates a mock database session for testing."""
    session = AsyncMock(spec=AsyncSession)

    async def mock_execute(query):
        result = MagicMock()
        query_str = str(query)

        # Handle get_tickers_by_ids query
        if "SELECT stock.ticker" in query_str and hasattr(query, "compile"):
            compiled = query.compile()
            if hasattr(compiled.params, "get"):
                stock_ids = compiled.params.get("id_1", [])
                mock_rows = [(f"STOCK_{id}",) for id in stock_ids]
                result.__iter__.return_value = iter(mock_rows)
                return result

        # Handle stock lookup by ID
        if "stock" in query_str.lower() and "WHERE" in query_str:
            stock = MagicMock()
            if hasattr(query.whereclause, "right"):
                stock.id = query.whereclause.right.value
            else:
                # Handle IN clause for multiple IDs
                stock.id = query.whereclause.right.clauses[0].value
            stock.ticker = f"STOCK_{stock.id}"
            result.scalar_one_or_none.return_value = stock
            return result

        return result

    session.execute = mock_execute
    session.commit = AsyncMock()
    return session


@pytest.fixture
def test_client(db_session):
    """Creates a test client with mock database."""

    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app)


@pytest.fixture
def order_book() -> OrderBook:
    """Creates a fresh order book for testing."""
    return OrderBook(stock_id=uuid4())


@pytest.fixture(autouse=True)
def mock_redis(monkeypatch):
    """Creates a mock Redis instance for testing."""
    mock_data = {}

    class MockRedis:
        def set(self, key: str, value: str) -> None:
            mock_data[key] = value

        def get(self, key: str) -> str | None:
            return mock_data.get(key)

        def keys(self, pattern: str) -> list[str]:
            if pattern == "order_book:*":
                return [k for k in mock_data.keys() if k.startswith("order_book:")]
            if pattern.endswith("*"):
                prefix = pattern[:-1]
                return [k for k in mock_data.keys() if k.startswith(prefix)]
            return [k for k in mock_data.keys() if k == pattern]

        def exists(self, key: str) -> bool:
            return key in mock_data

    order_book_cache.redis = MockRedis()  # type: ignore
    yield
    mock_data.clear()


@pytest.fixture(autouse=True)
async def create_test_database():
    """Creates test database tables."""
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
