from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import orjson
import pytest

from order_book_simulator.common.models import OrderSide, OrderType
from order_book_simulator.matching.matching_engine import MatchingEngine


def create_order(stock_id: UUID) -> dict[str, Any]:
    """Creates a test order with the specified stock ID."""
    return {
        "id": str(uuid4()),
        "stock_id": str(stock_id),
        "ticker": "TICKER",
        "price": Decimal("100"),
        "quantity": Decimal("10"),
        "side": OrderSide.BUY,
        "order_type": OrderType.LIMIT,
        "created_at": datetime.now(timezone.utc),
    }


@pytest.mark.asyncio
async def test_creates_order_book_for_new_stock(matching_engine: MatchingEngine):
    """Tests that the engine creates a new order book when needed."""
    stock_id = uuid4()
    order = create_order(stock_id)
    await matching_engine.process_order(order)

    assert stock_id in matching_engine.order_books


@pytest.mark.asyncio
async def test_publishes_market_data_on_trade(
    matching_engine: MatchingEngine, mock_kafka_producer: AsyncMock
):
    """
    Tests that market data publishes carry the correct topic, snapshot,
    and trade payload after a resting order is matched.
    """
    stock_id = uuid4()

    # Add a sell order.
    sell_order = create_order(stock_id)
    sell_order["side"] = OrderSide.SELL
    await matching_engine.process_order(sell_order)
    # Add a matching buy order.
    buy_order = create_order(stock_id)
    await matching_engine.process_order(buy_order)

    calls = mock_kafka_producer.send_and_wait.call_args_list
    assert len(calls) == 2

    # First publish - sell rests on the book, no trades.
    first_topic, first_bytes = calls[0].args
    assert first_topic == "market-data"
    first_payload = orjson.loads(first_bytes)
    assert first_payload["stock_id"] == str(stock_id)
    assert first_payload["ticker"] == "TICKER"
    assert first_payload["bids"] == []
    assert first_payload["asks"] == [
        {"price": "100", "quantity": "10", "order_count": 1}
    ]
    assert first_payload["trades"] == []

    # Second publish - buy lifts the offer, book empties, trade reported.
    second_topic, second_bytes = calls[1].args
    assert second_topic == "market-data"
    second_payload = orjson.loads(second_bytes)
    assert second_payload["stock_id"] == str(stock_id)
    assert second_payload["ticker"] == "TICKER"
    assert second_payload["bids"] == []
    assert second_payload["asks"] == []
    assert len(second_payload["trades"]) == 1
    trade = second_payload["trades"][0]
    assert trade["price"] == "100"
    assert trade["quantity"] == "10"
    assert trade["stock_id"] == str(stock_id)
    assert trade["buyer_order_id"] == str(buy_order["id"])
    assert trade["seller_order_id"] == str(sell_order["id"])
    assert "timestamp" in trade


@pytest.mark.asyncio
async def test_cancel_order_success(
    matching_engine: MatchingEngine, mock_kafka_producer: AsyncMock
):
    """Tests successful order cancellation through the matching engine."""
    stock_id = uuid4()
    order = create_order(stock_id)

    # Add order first.
    await matching_engine.process_order(order)
    assert stock_id in matching_engine.order_books

    # Cancel it.
    cancel_msg = {
        "type": "cancel",
        "order_id": order["id"],
        "stock_id": str(stock_id),
        "ticker": "TICKER",
    }
    result = await matching_engine.cancel_order(cancel_msg)

    assert result["success"] is True
    # Should have published market data for both the order and the cancellation.
    assert mock_kafka_producer.send_and_wait.call_count == 2


@pytest.mark.asyncio
async def test_cancel_order_not_found(matching_engine: MatchingEngine):
    """Tests cancelling an order that doesn't exist."""
    stock_id = uuid4()

    # Create an order book by adding an order.
    order = create_order(stock_id)
    await matching_engine.process_order(order)

    # Try to cancel a different order.
    cancel_msg = {
        "type": "cancel",
        "order_id": str(uuid4()),  # Different order ID.
        "stock_id": str(stock_id),
        "ticker": "TICKER",
    }
    result = await matching_engine.cancel_order(cancel_msg)

    assert result["success"] is False
    assert result["reason"] == "Order not found"


@pytest.mark.asyncio
async def test_cancel_order_book_not_found(matching_engine: MatchingEngine):
    """Tests cancelling an order for a stock with no order book."""
    cancel_msg = {
        "type": "cancel",
        "order_id": str(uuid4()),
        "stock_id": str(uuid4()),  # No order book exists for this stock.
        "ticker": "TICKER",
    }
    result = await matching_engine.cancel_order(cancel_msg)

    assert result["success"] is False
    assert result["reason"] == "Order book not found"
