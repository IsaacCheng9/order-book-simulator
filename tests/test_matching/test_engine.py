from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest

from order_book_simulator.common.models import OrderSide, OrderType
from order_book_simulator.matching.engine import MatchingEngine
from tests.conftest import MockMarketDataPublisher


def create_order(instrument_id: UUID) -> dict[str, Any]:
    """Creates a test order with the specified instrument ID."""
    return {
        "id": str(uuid4()),
        "instrument_id": str(instrument_id),
        "price": Decimal("100"),
        "quantity": Decimal("10"),
        "side": OrderSide.BUY,
        "type": OrderType.LIMIT,
        "created_at": datetime.now(timezone.utc),
    }


@pytest.mark.asyncio
async def test_creates_order_book_for_new_instrument(matching_engine: MatchingEngine):
    """Tests that the engine creates a new order book when needed."""
    instrument_id = uuid4()
    order = create_order(instrument_id)
    await matching_engine.process_order(order)

    assert instrument_id in matching_engine.order_books


@pytest.mark.asyncio
async def test_publishes_market_data_on_trade(
    matching_engine: MatchingEngine, market_data_publisher: MockMarketDataPublisher
):
    """Tests that market data updates are published when trades occur."""
    instrument_id = uuid4()

    # Add a sell order.
    sell_order = create_order(instrument_id)
    sell_order["side"] = OrderSide.SELL
    await matching_engine.process_order(sell_order)
    # Add a matching buy order.
    buy_order = create_order(instrument_id)
    await matching_engine.process_order(buy_order)

    assert len(market_data_publisher.published_updates) == 1
    published_instrument_id, market_data = market_data_publisher.published_updates[0]
    assert published_instrument_id == instrument_id
    assert "trades" in market_data
    assert len(market_data["trades"]) == 1
