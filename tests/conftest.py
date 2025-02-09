from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from starlette.testclient import TestClient

from order_book_simulator.gateway.app import app, app_state
from order_book_simulator.matching.engine import MatchingEngine
from order_book_simulator.matching.order_book import OrderBook
from order_book_simulator.matching.service import matching_service


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


@pytest.fixture(autouse=True)
def reset_matching_service():
    """Resets the matching service state before each test."""
    matching_service.engine = None
    yield
    matching_service.engine = None


@pytest.fixture
def matching_engine(market_data_publisher, reset_matching_service) -> MatchingEngine:
    """Creates a matching engine instance for testing."""
    engine = MatchingEngine(market_data_publisher)
    # Set the engine in both app_state and matching_service for API tests.
    app_state.matching_engine = engine
    matching_service.engine = engine
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
