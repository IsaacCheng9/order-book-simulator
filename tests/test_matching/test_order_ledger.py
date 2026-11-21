from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import orjson
import pytest

from order_book_simulator.common.cache import order_book_cache
from order_book_simulator.common.events import (
    EVENT_SCHEMA_VERSION,
    validate_market_data_event,
)
from order_book_simulator.common.models import (
    OrderRecord,
    OrderSide,
    OrderStatus,
    OrderType,
)
from order_book_simulator.gateway.producer import OrderProducer
from order_book_simulator.market_data.analytics import MarketDataAnalytics
from order_book_simulator.matching import order_consumer as order_consumer_module
from order_book_simulator.matching.matching_engine import MatchingEngine
from order_book_simulator.matching.order_book import OrderRejectionError
from order_book_simulator.matching.order_consumer import OrderConsumer
from tests.test_matching.test_restart_recovery import (
    InMemoryKafkaCluster,
    ReplayableKafkaConsumer,
)


@pytest.mark.asyncio
async def test_gateway_producer_publishes_versioned_order_and_cancel_metadata():
    """Carry schema_version, user_id, time_in_force, and client_order_id."""
    producer = OrderProducer()
    mock_kafka = AsyncMock()
    producer.producer = mock_kafka

    order_id = uuid4()
    stock_id = uuid4()
    user_id = uuid4()
    record: OrderRecord = {
        "id": order_id,
        "stock_id": stock_id,
        "ticker": "AAPL",
        "user_id": user_id,
        "type": OrderType.LIMIT,
        "side": OrderSide.BUY,
        "price": Decimal("150.25"),
        "quantity": Decimal(20),
        "time_in_force": "GTC",
        "client_order_id": "client-ref-42",
        "status": OrderStatus.PENDING,
        "filled_quantity": Decimal(0),
        "total_fee": Decimal(0),
        "gateway_received_at": "2026-07-26T12:00:00+00:00",
    }

    await producer.send_order(record)
    await producer.cancel_order(order_id, stock_id, "AAPL")

    order_payload = orjson.loads(mock_kafka.send_and_wait.call_args_list[0].args[1])
    assert order_payload["schema_version"] == EVENT_SCHEMA_VERSION
    assert order_payload["user_id"] == str(user_id)
    assert order_payload["time_in_force"] == "GTC"
    assert order_payload["client_order_id"] == "client-ref-42"

    cancel_payload = orjson.loads(mock_kafka.send_and_wait.call_args_list[1].args[1])
    assert cancel_payload["schema_version"] == EVENT_SCHEMA_VERSION
    assert cancel_payload["order_id"] == str(order_id)


@pytest.mark.asyncio
async def test_matching_engine_emits_canonical_order_lifecycle_events(
    matching_engine: MatchingEngine,
    mock_kafka_producer: AsyncMock,
):
    """Emit validated PENDING, PARTIALLY_FILLED, FILLED, and CANCELLED updates."""
    stock_id = uuid4()
    seller_user = uuid4()
    buyer_user = uuid4()
    base = datetime(2026, 7, 26, 12, 0, 0, tzinfo=UTC)

    sell_id = str(uuid4())
    await matching_engine.process_order(
        {
            "schema_version": 1,
            "id": sell_id,
            "user_id": str(seller_user),
            "stock_id": str(stock_id),
            "ticker": "AAPL",
            "side": "SELL",
            "order_type": "LIMIT",
            "price": "100.00",
            "quantity": "10",
            "time_in_force": "GTC",
            "client_order_id": "sell-1",
            "gateway_received_at": base.isoformat(),
        }
    )

    # Partial fill of 4 shares via crossing limit buy.
    buy_1_id = str(uuid4())
    await matching_engine.process_order(
        {
            "schema_version": 1,
            "id": buy_1_id,
            "user_id": str(buyer_user),
            "stock_id": str(stock_id),
            "ticker": "AAPL",
            "side": "BUY",
            "order_type": "LIMIT",
            "price": "100.00",
            "quantity": "4",
            "time_in_force": "IOC",
            "client_order_id": "buy-1",
            "gateway_received_at": (base + timedelta(seconds=1)).isoformat(),
        }
    )

    # Cancel remaining 6 shares of the resting sell order.
    await matching_engine.cancel_order(
        {
            "schema_version": 1,
            "order_id": sell_id,
            "stock_id": str(stock_id),
            "ticker": "AAPL",
            "cancelled_at": (base + timedelta(seconds=2)).isoformat(),
        }
    )

    # Market order against an empty book finishes CANCELLED with 0 fills.
    mkt_id = str(uuid4())
    await matching_engine.process_order(
        {
            "schema_version": 1,
            "id": mkt_id,
            "user_id": str(buyer_user),
            "stock_id": str(stock_id),
            "ticker": "AAPL",
            "side": "BUY",
            "order_type": "MARKET",
            "price": None,
            "quantity": "5",
            "time_in_force": None,
            "client_order_id": "mkt-empty",
            "gateway_received_at": (base + timedelta(seconds=3)).isoformat(),
        }
    )

    events = [
        validate_market_data_event(orjson.loads(call.args[1]))
        for call in mock_kafka_producer.send_and_wait.call_args_list
    ]
    assert len(events) == 4

    # Event 0: resting sell order.
    assert events[0]["schema_version"] == 1
    assert len(events[0]["order_updates"]) == 1
    resting = events[0]["order_updates"][0]
    assert resting["id"] == sell_id
    assert resting["user_id"] == str(seller_user)
    assert resting["status"] == "PENDING"
    assert resting["filled_quantity"] == "0"
    assert resting["time_in_force"] == "GTC"
    assert resting["client_order_id"] == "sell-1"

    # Event 1: resting sell becomes PARTIALLY_FILLED, incoming buy is FILLED.
    by_id_1 = {u["id"]: u for u in events[1]["order_updates"]}
    assert by_id_1[sell_id]["status"] == "PARTIALLY_FILLED"
    assert by_id_1[sell_id]["filled_quantity"] == "4"
    assert by_id_1[sell_id]["quantity"] == "10"
    assert by_id_1[buy_1_id]["status"] == "FILLED"
    assert by_id_1[buy_1_id]["filled_quantity"] == "4"
    assert by_id_1[buy_1_id]["user_id"] == str(buyer_user)

    # Event 2: cancelled resting sell preserves real filled_quantity and metadata.
    cancelled = events[2]["order_updates"][0]
    assert cancelled["id"] == sell_id
    assert cancelled["status"] == "CANCELLED"
    assert cancelled["filled_quantity"] == "4"
    assert cancelled["quantity"] == "10"
    assert cancelled["user_id"] == str(seller_user)
    assert cancelled["client_order_id"] == "sell-1"
    assert cancelled["created_at"] == base.isoformat()
    assert cancelled["updated_at"] == (base + timedelta(seconds=2)).isoformat()

    # Event 3: unfilled market order ends CANCELLED with null price.
    mkt_update = events[3]["order_updates"][0]
    assert mkt_update["id"] == mkt_id
    assert mkt_update["type"] == "MARKET"
    assert mkt_update["price"] is None
    assert mkt_update["status"] == "CANCELLED"
    assert mkt_update["filled_quantity"] == "0"


@pytest.mark.asyncio
async def test_restart_replay_preserves_order_metadata_for_post_restart_events(
    monkeypatch: pytest.MonkeyPatch,
):
    """Preserve user_id, client_order_id, and partial fills across Kafka replay."""
    stock_id = uuid4()
    seller_user = uuid4()
    buyer_user = uuid4()
    sell_id = str(uuid4())
    base = datetime(2026, 7, 26, 12, 0, 0, tzinfo=UTC)

    cluster = InMemoryKafkaCluster()
    # Committed before restart: resting sell + partial fill of 3 shares.
    cluster.append(
        {
            "schema_version": 1,
            "type": "order",
            "id": sell_id,
            "user_id": str(seller_user),
            "stock_id": str(stock_id),
            "ticker": "AAPL",
            "side": "SELL",
            "order_type": "LIMIT",
            "price": "100.00",
            "quantity": "10",
            "time_in_force": "GTC",
            "client_order_id": "persistent-cid",
            "gateway_received_at": base.isoformat(),
        }
    )
    cluster.append(
        {
            "schema_version": 1,
            "type": "order",
            "id": str(uuid4()),
            "user_id": str(buyer_user),
            "stock_id": str(stock_id),
            "ticker": "AAPL",
            "side": "BUY",
            "order_type": "LIMIT",
            "price": "100.00",
            "quantity": "3",
            "gateway_received_at": (base + timedelta(seconds=1)).isoformat(),
        }
    )
    # Uncommitted after restart: cancel remaining 7 shares.
    cluster.append(
        {
            "schema_version": 1,
            "type": "cancel",
            "order_id": sell_id,
            "stock_id": str(stock_id),
            "ticker": "AAPL",
            "cancelled_at": (base + timedelta(seconds=2)).isoformat(),
        }
    )

    monkeypatch.setattr(
        order_consumer_module, "AIOKafkaConsumer", ReplayableKafkaConsumer
    )
    ReplayableKafkaConsumer.cluster = cluster
    ReplayableKafkaConsumer.stop_at_offset = None
    cluster.committed_offset = 2

    producer = AsyncMock()
    engine = MatchingEngine(producer, MarketDataAnalytics(order_book_cache.redis))
    await OrderConsumer(matching_engine=engine).start()

    assert producer.send_and_wait.call_count == 1
    post_restart_event = validate_market_data_event(
        orjson.loads(producer.send_and_wait.call_args.args[1])
    )
    update = post_restart_event["order_updates"][0]
    assert update["id"] == sell_id
    assert update["user_id"] == str(seller_user)
    assert update["time_in_force"] == "GTC"
    assert update["client_order_id"] == "persistent-cid"
    assert update["quantity"] == "10"
    assert update["filled_quantity"] == "3"
    assert update["status"] == "CANCELLED"
    assert update["created_at"] == base.isoformat()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_fields",
    [
        {"schema_version": 2},
        {"user_id": "not-a-uuid"},
        {"ticker": "A" * 21},
        {"ticker": "AA\x00PL"},
        {"time_in_force": "X" * 21},
        {"time_in_force": "GT\x00C"},
        {"client_order_id": "C" * 101},
        {"client_order_id": "cid\x001"},
    ],
)
async def test_order_consumer_skips_invalid_metadata_envelope(
    bad_fields: dict[str, Any],
):
    """Skip orders with invalid schema_version or metadata before matching."""
    engine = AsyncMock()
    consumer = OrderConsumer(matching_engine=engine)
    payload: dict[str, Any] = {
        "schema_version": 1,
        "type": "order",
        "id": str(uuid4()),
        "user_id": str(uuid4()),
        "stock_id": str(uuid4()),
        "ticker": "AAPL",
        "side": "BUY",
        "order_type": "LIMIT",
        "price": "100",
        "quantity": "10",
        "gateway_received_at": datetime.now(UTC).isoformat(),
    }
    payload.update(bad_fields)

    record = InMemoryKafkaCluster().append(payload)
    await consumer._process_message(record)

    engine.process_order.assert_not_awaited()
    assert UUID(payload["id"])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "price,quantity",
    [
        ("999999999999.999999995", "1"),
        ("0.000000001", "1"),
        ("999999.99999999", "1000000.00000001"),
    ],
)
async def test_order_book_rejects_orders_violating_numeric_20_8_bounds(
    matching_engine: MatchingEngine,
    price: str,
    quantity: str,
):
    """Reject orders that would fail NUMERIC(20, 8) validation downstream."""
    with pytest.raises(OrderRejectionError):
        await matching_engine.process_order(
            {
                "id": str(uuid4()),
                "stock_id": str(uuid4()),
                "ticker": "AAPL",
                "side": "BUY",
                "order_type": "LIMIT",
                "price": price,
                "quantity": quantity,
                "gateway_received_at": datetime.now(UTC).isoformat(),
            }
        )


@pytest.mark.asyncio
async def test_order_book_rejects_aggregated_price_level_overflow(
    matching_engine: MatchingEngine,
):
    """Reject a resting order whose addition would overflow PriceLevel quantity."""
    stock_id = uuid4()
    now = datetime.now(UTC).isoformat()
    await matching_engine.process_order(
        {
            "id": str(uuid4()),
            "stock_id": str(stock_id),
            "ticker": "AAPL",
            "side": "BUY",
            "order_type": "LIMIT",
            "price": "1",
            "quantity": "600000000000",
            "gateway_received_at": now,
        }
    )

    with pytest.raises(OrderRejectionError, match="price level quantity"):
        await matching_engine.process_order(
            {
                "id": str(uuid4()),
                "stock_id": str(stock_id),
                "ticker": "AAPL",
                "side": "BUY",
                "order_type": "LIMIT",
                "price": "1",
                "quantity": "600000000000",
                "gateway_received_at": now,
            }
        )

    assert matching_engine.order_books[stock_id].bid_levels[Decimal(1)].quantity == (
        Decimal(600000000000)
    )


@pytest.mark.asyncio
async def test_order_consumer_skips_overlong_ticker_on_cancel():
    """Reject cancel messages whose ticker exceeds VARCHAR(20) before matching."""
    engine = AsyncMock()
    consumer = OrderConsumer(matching_engine=engine)
    record = InMemoryKafkaCluster().append(
        {
            "schema_version": 1,
            "type": "cancel",
            "order_id": str(uuid4()),
            "stock_id": str(uuid4()),
            "ticker": "A" * 21,
        }
    )

    await consumer._process_message(record)
    engine.cancel_order.assert_not_awaited()
