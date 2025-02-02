from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from order_book_simulator.gateway.app import app


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
