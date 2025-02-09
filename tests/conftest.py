from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from starlette.testclient import TestClient

from order_book_simulator.common.cache import order_book_cache
from order_book_simulator.gateway.app import app, app_state
from order_book_simulator.matching.engine import MatchingEngine
from order_book_simulator.matching.order_book import OrderBook


class MockMarketDataPublisher:
    def __init__(self):
        self.published_updates = []

    async def __call__(self, instrument_id: UUID, market_data: dict) -> None:
        self.published_updates.append((instrument_id, market_data))


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


@pytest.fixture
def test_client(mock_kafka_producer, monkeypatch):
    """Creates a test client for API testing."""

    # Mock the AIOKafkaProducer constructor
    def mock_producer_init(*args, **kwargs):
        return mock_kafka_producer

    monkeypatch.setattr(
        "order_book_simulator.gateway.producer.AIOKafkaProducer", mock_producer_init
    )

    with TestClient(app) as client:
        yield client


@pytest.fixture
def order_book() -> OrderBook:
    """Creates a fresh order book for testing."""
    return OrderBook(instrument_id=uuid4())


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
            if pattern.endswith("*"):
                prefix = pattern[:-1]
                return [k for k in mock_data.keys() if k.startswith(prefix)]
            return [k for k in mock_data.keys() if k == pattern]

    # Replace the Redis instance in the cache module directly
    order_book_cache.redis = MockRedis()  # type: ignore
    yield
    mock_data.clear()
