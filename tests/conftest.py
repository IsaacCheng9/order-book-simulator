from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from order_book_simulator.gateway.app import app
from order_book_simulator.matching.engine import MatchingEngine
from order_book_simulator.matching.order_book import OrderBook


class MockMarketDataPublisher:
    def __init__(self):
        self.published_updates = []

    async def __call__(self, instrument_id: UUID, market_data: dict) -> None:
        self.published_updates.append((instrument_id, market_data))


@pytest.fixture(autouse=True)
def mock_kafka_producer():
    # Create a mock Kafka producer with the necessary async methods.
    producer_mock = AsyncMock()
    producer_mock.start = AsyncMock()
    producer_mock.stop = AsyncMock()
    producer_mock.send_and_wait = AsyncMock()
    return producer_mock


@pytest.fixture(autouse=True)
def test_client(mock_kafka_producer, monkeypatch):
    # Mock the AIOKafkaProducer constructor to return our mock Kafka producer.
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


@pytest.fixture
def market_data_publisher():
    """Creates a mock market data publisher for testing."""
    return MockMarketDataPublisher()


@pytest.fixture
def matching_engine(market_data_publisher) -> MatchingEngine:
    """Creates a matching engine instance for testing."""
    return MatchingEngine(market_data_publisher)
