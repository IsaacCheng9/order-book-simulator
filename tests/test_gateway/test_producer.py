from datetime import UTC, datetime
from decimal import Decimal
from typing import cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from aiokafka import AIOKafkaProducer
from aiokafka.errors import KafkaConnectionError
from fastapi import HTTPException

from order_book_simulator.common.models import (
    OrderRecord,
    OrderSide,
    OrderStatus,
    OrderType,
)
from order_book_simulator.gateway.producer import OrderProducer
from order_book_simulator.market_data.db_consumer import MarketDataDBConsumer
from order_book_simulator.matching.matching_engine import MatchingEngine
from order_book_simulator.matching.order_consumer import OrderConsumer


@pytest.mark.asyncio
async def test_send_order_hides_kafka_internals_in_http_500():
    """Return a generic 500 detail when Kafka publish fails on order submit."""
    producer = OrderProducer()
    mock_kafka = AsyncMock()
    mock_kafka.send_and_wait.side_effect = KafkaConnectionError(
        "broker-internal-host:9092 refused"
    )
    producer.producer = cast(AIOKafkaProducer, mock_kafka)

    order_record: OrderRecord = {
        "id": uuid4(),
        "stock_id": uuid4(),
        "ticker": "AAPL",
        "user_id": uuid4(),
        "type": OrderType.LIMIT,
        "side": OrderSide.BUY,
        "price": Decimal("100.00"),
        "quantity": Decimal(10),
        "time_in_force": "GTC",
        "client_order_id": None,
        "status": OrderStatus.PENDING,
        "filled_quantity": Decimal(0),
        "total_fee": Decimal(0),
        "gateway_received_at": datetime.now(UTC).isoformat(),
    }

    with pytest.raises(HTTPException) as exc_info:
        await producer.send_order(order_record)

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "Failed to submit order"
    assert "broker-internal-host" not in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_cancel_order_hides_kafka_internals_in_http_500():
    """Return a generic 500 detail when Kafka publish fails on cancel."""
    producer = OrderProducer()
    mock_kafka = AsyncMock()
    mock_kafka.send_and_wait.side_effect = KafkaConnectionError(
        "broker-internal-host:9092 refused"
    )
    producer.producer = cast(AIOKafkaProducer, mock_kafka)

    with pytest.raises(HTTPException) as exc_info:
        await producer.cancel_order(uuid4(), uuid4(), "AAPL")

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "Failed to submit order cancellation"
    assert "broker-internal-host" not in str(exc_info.value.detail)


def test_kafka_clients_respect_bootstrap_servers_env_var(
    monkeypatch: pytest.MonkeyPatch,
):
    """Use KAFKA_BOOTSTRAP_SERVERS when bootstrap_servers is not passed."""
    monkeypatch.setenv("KAFKA_BOOTSTRAP_SERVERS", "kafka-broker:19092")

    producer = OrderProducer()
    order_consumer = OrderConsumer(matching_engine=cast(MatchingEngine, object()))
    db_consumer = MarketDataDBConsumer()

    assert producer.bootstrap_servers == "kafka-broker:19092"
    assert order_consumer.bootstrap_servers == "kafka-broker:19092"
    assert db_consumer.bootstrap_servers == "kafka-broker:19092"

    # Explicit constructor arguments take precedence over the environment.
    explicit_producer = OrderProducer(bootstrap_servers="explicit:9092")
    assert explicit_producer.bootstrap_servers == "explicit:9092"
