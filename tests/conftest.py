from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from starlette.testclient import TestClient

from order_book_simulator.common.cache import order_book_cache
from order_book_simulator.database.connection import get_db
from order_book_simulator.database.db_models import Base
from order_book_simulator.gateway.app import app, app_state
from order_book_simulator.matching.engine import MatchingEngine
from order_book_simulator.matching.order_book import OrderBook

# Add test database configuration
test_engine = create_async_engine("postgresql+asyncpg://test:test@localhost:5432/test")
TestingSessionLocal = async_sessionmaker(
    test_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


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

        # Handle get_all_stocks query first (returns ticker, company_name
        # pairs)
        if (
            "company_name" in query_str.lower()
            and "ticker" in query_str.lower()
            and "WHERE" not in query_str.upper()
        ):
            mock_rows = [
                ("AAPL", "Apple Inc."),
                ("GOOGL", "Alphabet Inc."),
                ("MSFT", "Microsoft Corporation"),
                ("TSLA", "Tesla Inc."),
            ]
            result.__iter__.return_value = iter(mock_rows)
            return result

        # Handle get_tickers_by_ids query
        if "SELECT stock.ticker" in query_str and hasattr(query, "compile"):
            compiled = query.compile()
            if hasattr(compiled.params, "get"):
                stock_ids = compiled.params.get("id_1", [])
                mock_rows = [(f"STOCK_{id}",) for id in stock_ids]
                result.__iter__.return_value = iter(mock_rows)
                return result

        # Handle get_stock_id_ticker_mapping query (returns id, ticker pairs)
        if "SELECT stock.id, stock.ticker" in query_str and hasattr(query, "compile"):
            compiled = query.compile()
            if hasattr(compiled.params, "get"):
                stock_ids = compiled.params.get("id_1", [])
                mock_rows = [(id, f"STOCK_{id}") for id in stock_ids]
                result.__iter__.return_value = iter(mock_rows)
                return result

        # Handle stock lookup by ID or ticker
        if (
            "stock" in query_str.lower()
            and "WHERE" in query_str
            and "trade" not in query_str.lower()
        ):
            stock = MagicMock()
            stock_value = None

            # Try to extract the value from the where clause
            if hasattr(query, "whereclause") and hasattr(query.whereclause, "right"):
                stock_value = query.whereclause.right.value

            if isinstance(stock_value, str):
                # Ticker lookup
                stock.id = uuid4()
                stock.ticker = stock_value
            elif stock_value:
                # ID lookup
                stock.id = stock_value
                stock.ticker = f"STOCK_{stock_value}"
            else:
                # Fallback
                stock.id = uuid4()
                stock.ticker = "AAPL"

            result.scalar_one_or_none.return_value = stock
            return result

        # Handle trade queries - get_recent_trades
        if (
            "SELECT trade" in query_str
            and "stock.ticker" in query_str
            and "JOIN" in query_str.upper()
        ):
            # Mock recent trades across all stocks
            mock_trade_rows = []
            for i in range(3):  # Return 3 mock trades
                mock_trade = MagicMock()
                mock_trade.id = uuid4()
                mock_trade.stock_id = uuid4()
                mock_trade.price = f"{100 + i}.00"
                mock_trade.quantity = f"{10 + i}.00"
                mock_trade.total_amount = f"{(100 + i) * (10 + i)}.00"
                mock_trade.trade_time = datetime.now(timezone.utc)
                mock_trade.buyer_order_id = uuid4()
                mock_trade.seller_order_id = uuid4()

                mock_ticker = f"STOCK_{i}"
                mock_trade_rows.append((mock_trade, mock_ticker))

            result.all.return_value = mock_trade_rows
            return result

        # Handle trade queries - get_trades_by_stock
        if (
            "SELECT trade" in query_str
            and "stock_id" in query_str
            and "ORDER BY" in query_str.upper()
        ):
            # Mock trades for specific stock
            mock_trades = []
            for i in range(2):  # Return 2 mock trades
                mock_trade = MagicMock()
                mock_trade.id = uuid4()
                mock_trade.stock_id = uuid4()
                mock_trade.price = f"{150 + i}.00"
                mock_trade.quantity = f"{5 + i}.00"
                mock_trade.total_amount = f"{(150 + i) * (5 + i)}.00"
                mock_trade.trade_time = datetime.now(timezone.utc)
                mock_trade.buyer_order_id = uuid4()
                mock_trade.seller_order_id = uuid4()
                mock_trades.append(mock_trade)

            result.scalars.return_value.all.return_value = mock_trades
            return result

        # Handle trade analytics queries
        if (
            "func.count" in query_str.lower() or "COUNT" in query_str.upper()
        ) and "trade" in query_str.lower():
            mock_row = MagicMock()

            # Check if this is a global analytics query (no WHERE clause with stock_id)
            if "stock_id" not in query_str.lower() or "WHERE" not in query_str.upper():
                # Global analytics - return larger numbers for all stocks
                mock_row.trade_count = 50
                mock_row.total_volume = 5000.00
                mock_row.total_value = 750000.00
                mock_row.avg_quantity = 100.00
                mock_row.avg_price = 150.00
                mock_row.min_price = 100.00
                mock_row.max_price = 200.00
            else:
                # Single stock analytics
                mock_row.trade_count = 10
                mock_row.total_volume = 1000.00
                mock_row.total_value = 150000.00
                mock_row.avg_price = 150.00
                mock_row.min_price = 145.00
                mock_row.max_price = 155.00

            result.first.return_value = mock_row
            return result

        # Handle empty trade analytics queries (simulate NULL values)
        if "empty_trades" in query_str.lower():
            mock_row = MagicMock()
            mock_row.trade_count = 0
            mock_row.total_volume = None
            mock_row.total_value = None
            mock_row.avg_quantity = None
            mock_row.avg_price = None
            mock_row.min_price = None
            mock_row.max_price = None
            result.first.return_value = mock_row
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
def mock_redis():
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
