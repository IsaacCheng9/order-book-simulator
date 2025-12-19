from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import cast
from uuid import uuid4

import pytest

from order_book_simulator.common.models import OrderBookState
from order_book_simulator.market_data.analytics import MarketDataAnalytics
from tests.conftest import create_price_level

# add stream storage next to mock_data
streams: dict[str, list[tuple[str, dict]]] = {}


@pytest.mark.asyncio
async def test_record_state_and_depth(redis_stream_client):
    stock_id = uuid4()
    analytics = MarketDataAnalytics(redis_stream_client)

    now = datetime.now(timezone.utc)

    state = OrderBookState(
        stock_id=stock_id,
        ticker="AAPL",
        bids=[
            create_price_level(price=Decimal("100.00"), quantity=Decimal("10")),
            create_price_level(price=Decimal("99.50"), quantity=Decimal("5")),
        ],
        asks=[
            create_price_level(price=Decimal("101.00"), quantity=Decimal("7")),
        ],
        last_trade_price=Decimal("100.50"),
        last_trade_quantity=Decimal("2"),
        last_update_time=now,
    )
    await analytics.record_state(state)

    depth = await analytics.get_market_depth(stock_id)
    # Sum of bid quantity is 15.
    assert depth["bid_depth"] == Decimal("15")
    # Sum of ask quantity is 7.
    assert depth["ask_depth"] == Decimal("7")
    # Difference between bid and ask prices is 1.00.
    assert depth["spread"] == Decimal("1.00")
    # Middle of the highest bid and lowest ask is 100.50.
    assert depth["mid_price"] == Decimal("100.50")


@pytest.mark.asyncio
async def test_get_vwap(redis_stream_client):
    stock_id = uuid4()
    analytics = MarketDataAnalytics(redis_stream_client)

    base = datetime.now(timezone.utc)

    # Two trades: (100 * 2) and (102 * 3) => VWAP = (200 + 306) / (2 + 3) = 101.2
    trades = [(Decimal("100"), Decimal("2")), (Decimal("102"), Decimal("3"))]
    for index, (price, quantity) in enumerate(trades):
        state = OrderBookState(
            stock_id=stock_id,
            ticker="AAPL",
            bids=[],
            asks=[],
            last_trade_price=price,
            last_trade_quantity=quantity,
            last_update_time=base + timedelta(seconds=index),
        )
        await analytics.record_state(state)

    vwap = cast(
        Decimal, await analytics.get_vwap(stock_id, window=timedelta(minutes=5))
    )
    # Polars returns Decimal; compare numerically
    assert float(vwap) == pytest.approx(101.2, rel=1e-9)
