from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest

from order_book_simulator.common.cache import order_book_cache
from order_book_simulator.common.models import OrderSide, OrderType
from order_book_simulator.matching.matching_engine import MatchingEngine


def create_order(
    stock_id: UUID,
    side: OrderSide = OrderSide.BUY,
    price: Decimal = Decimal("100"),
    quantity: Decimal = Decimal("10"),
) -> dict[str, Any]:
    """Creates a test order with the specified stock ID."""
    return {
        "id": str(uuid4()),
        "stock_id": str(stock_id),
        "ticker": "TICKER",
        "price": price,
        "quantity": quantity,
        "side": side,
        "order_type": OrderType.LIMIT,
        "created_at": datetime.now(timezone.utc),
    }


@pytest.mark.asyncio
async def test_process_order_stores_deltas(
    matching_engine: MatchingEngine,
):
    """Tests that processing a resting order stores a LEVEL_UPDATE delta."""
    stock_id = uuid4()
    order = create_order(stock_id)
    await matching_engine.process_order(order)

    seq = await order_book_cache.get_current_delta_sequence_number(stock_id)
    assert seq > 0

    deltas = await order_book_cache.get_deltas_since(stock_id, 0)
    assert deltas is not None
    assert len(deltas) == 1
    assert deltas[0]["delta_type"] == "LEVEL_UPDATE"


@pytest.mark.asyncio
async def test_process_order_stores_trade_deltas(
    matching_engine: MatchingEngine,
):
    """
    Tests that matching orders stores TRADE and LEVEL_REMOVE deltas.
    """
    stock_id = uuid4()
    sell = create_order(stock_id, side=OrderSide.SELL)
    buy = create_order(stock_id, side=OrderSide.BUY)
    await matching_engine.process_order(sell)
    await matching_engine.process_order(buy)

    deltas = await order_book_cache.get_deltas_since(stock_id, 0)
    assert deltas is not None
    delta_types = [d["delta_type"] for d in deltas]
    # First order rests (LEVEL_UPDATE), second order matches
    # (TRADE + LEVEL_REMOVE).
    assert "LEVEL_UPDATE" in delta_types
    assert "TRADE" in delta_types
    assert "LEVEL_REMOVE" in delta_types


@pytest.mark.asyncio
async def test_cancel_order_stores_deltas(
    matching_engine: MatchingEngine,
):
    """Tests that cancelling an order stores a LEVEL_REMOVE delta."""
    stock_id = uuid4()
    order = create_order(stock_id)
    await matching_engine.process_order(order)

    # Record the sequence number before cancellation.
    seq_before = await order_book_cache.get_current_delta_sequence_number(stock_id)

    cancel_msg = {
        "type": "cancel",
        "order_id": order["id"],
        "stock_id": str(stock_id),
        "ticker": "TICKER",
    }
    result = await matching_engine.cancel_order(cancel_msg)
    assert result["success"] is True

    # New deltas should have been stored after the cancellation.
    deltas = await order_book_cache.get_deltas_since(stock_id, seq_before)
    assert deltas is not None
    assert len(deltas) == 1
    assert deltas[0]["delta_type"] == "LEVEL_REMOVE"


@pytest.mark.asyncio
async def test_cancel_partial_level_stores_level_update(
    matching_engine: MatchingEngine,
):
    """
    Tests that cancelling one of two orders at a level stores a
    LEVEL_UPDATE delta (not LEVEL_REMOVE).
    """
    stock_id = uuid4()
    order1 = create_order(stock_id)
    order2 = create_order(stock_id)
    await matching_engine.process_order(order1)
    await matching_engine.process_order(order2)

    seq_before = await order_book_cache.get_current_delta_sequence_number(stock_id)

    cancel_msg = {
        "type": "cancel",
        "order_id": order1["id"],
        "stock_id": str(stock_id),
        "ticker": "TICKER",
    }
    result = await matching_engine.cancel_order(cancel_msg)
    assert result["success"] is True

    deltas = await order_book_cache.get_deltas_since(stock_id, seq_before)
    assert deltas is not None
    assert len(deltas) == 1
    assert deltas[0]["delta_type"] == "LEVEL_UPDATE"
    assert deltas[0]["quantity"] == "10"
