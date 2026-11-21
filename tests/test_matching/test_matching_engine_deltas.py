from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, cast
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest

from order_book_simulator.common.cache import order_book_cache
from order_book_simulator.common.models import MAX_DELTA_HISTORY, OrderSide, OrderType
from order_book_simulator.matching.matching_engine import MatchingEngine
from order_book_simulator.matching.order_book import OrderBook
from order_book_simulator.multicast.multicast_publisher import MulticastPublisher


def create_order(
    stock_id: UUID,
    side: OrderSide = OrderSide.BUY,
    price: Decimal = Decimal(100),
    quantity: Decimal = Decimal(10),
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
        "created_at": datetime.now(UTC),
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


@pytest.mark.asyncio
async def test_cancel_order_publishes_on_order_book_ticker(
    matching_engine: MatchingEngine,
    monkeypatch: pytest.MonkeyPatch,
):
    """Uses the order book's canonical ticker rather than raw cancel payload."""
    stock_id = uuid4()
    order = create_order(stock_id)
    await matching_engine.process_order(order)

    published_channels: list[str] = []
    original_publish = order_book_cache.publish_deltas

    async def spy_publish(ticker: str, deltas):
        published_channels.append(ticker)
        await original_publish(ticker, deltas)

    monkeypatch.setattr(order_book_cache, "publish_deltas", spy_publish)

    cancel_msg = {
        "type": "cancel",
        "order_id": order["id"],
        "stock_id": str(stock_id),
        "ticker": "wrong_ticker",
    }
    result = await matching_engine.cancel_order(cancel_msg)

    assert result["success"] is True
    assert published_channels == ["TICKER"]


@pytest.mark.asyncio
async def test_large_sweep_above_delta_history_publishes_all_deltas(
    matching_engine: MatchingEngine,
    monkeypatch: pytest.MonkeyPatch,
):
    """Publishes every delta even when one sweep exceeds MAX_DELTA_HISTORY."""
    stock_id = uuid4()
    level_count = (MAX_DELTA_HISTORY // 2) + 200
    book = OrderBook(stock_id, "TICKER")
    matching_engine.order_books[stock_id] = book

    # Seed resting sells directly so the test exercises one sweep > 2,000 deltas.
    for index in range(level_count):
        book.add_order(
            {
                "id": str(uuid4()),
                "price": Decimal(100) + Decimal(index) * Decimal("0.01"),
                "quantity": Decimal(1),
                "side": OrderSide.SELL,
                "order_type": OrderType.LIMIT,
                "created_at": datetime.now(UTC),
            }
        )
    seq_before_sweep = book.delta_buffer.current_sequence

    published_batches: list[list[Any]] = []
    original_publish = order_book_cache.publish_deltas

    async def spy_publish(ticker: str, deltas):
        published_batches.append(list(deltas))
        await original_publish(ticker, deltas)

    monkeypatch.setattr(order_book_cache, "publish_deltas", spy_publish)

    mock_multicast = MagicMock(spec=MulticastPublisher)
    matching_engine.multicast_publisher = cast(MulticastPublisher, mock_multicast)

    sweep_order = {
        "id": str(uuid4()),
        "stock_id": str(stock_id),
        "ticker": "TICKER",
        "price": None,
        "quantity": Decimal(level_count),
        "side": OrderSide.BUY,
        "order_type": OrderType.MARKET,
    }
    await matching_engine.process_order(sweep_order)

    expected_deltas = level_count * 2
    assert expected_deltas > MAX_DELTA_HISTORY
    assert len(published_batches) == 1
    assert len(published_batches[0]) == expected_deltas
    assert mock_multicast.send.call_count == expected_deltas

    latest_seq = await order_book_cache.get_current_delta_sequence_number(stock_id)
    assert latest_seq == seq_before_sweep + expected_deltas
    retained = await order_book_cache.get_deltas_since(
        stock_id, latest_seq - MAX_DELTA_HISTORY
    )
    assert retained is not None
    assert len(retained) == MAX_DELTA_HISTORY
