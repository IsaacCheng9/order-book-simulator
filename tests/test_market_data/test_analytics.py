from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from order_book_simulator.common.models import OrderBookState, OrderSide, OrderType
from order_book_simulator.market_data.analytics import MarketDataAnalytics
from order_book_simulator.matching.matching_engine import MatchingEngine
from tests.conftest import create_price_level

# add stream storage next to mock_data
streams: dict[str, list[tuple[str, dict]]] = {}


@pytest.mark.asyncio
async def test_record_state_and_depth(redis_stream_client):
    stock_id = uuid4()
    analytics = MarketDataAnalytics(redis_stream_client)

    now = datetime.now(UTC)

    state = OrderBookState(
        stock_id=stock_id,
        ticker="AAPL",
        bids=[
            create_price_level(price=Decimal("100.00"), quantity=Decimal(10)),
            create_price_level(price=Decimal("99.50"), quantity=Decimal(5)),
        ],
        asks=[
            create_price_level(price=Decimal("101.00"), quantity=Decimal(7)),
        ],
        last_trade_price=Decimal("100.50"),
        last_trade_quantity=Decimal(2),
        last_update_time=now,
    )
    await analytics.record_state(state)

    depth = await analytics.get_market_depth(stock_id)
    # Sum of bid quantity is 15.
    assert depth["bid_depth"] == Decimal(15)
    # Sum of ask quantity is 7.
    assert depth["ask_depth"] == Decimal(7)
    # Difference between bid and ask prices is 1.00.
    assert depth["spread"] == Decimal("1.00")
    # Middle of the highest bid and lowest ask is 100.50.
    assert depth["mid_price"] == Decimal("100.50")


@pytest.mark.asyncio
async def test_get_vwap(redis_stream_client):
    stock_id = uuid4()
    analytics = MarketDataAnalytics(redis_stream_client)

    base = datetime.now(UTC)

    # Two trades: (100 * 2) and (102 * 3) => VWAP = (200 + 306) / (2 + 3) = 101.2
    trades = [(Decimal(100), Decimal(2)), (Decimal(102), Decimal(3))]
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

    vwap = await analytics.get_vwap(stock_id, window=timedelta(minutes=5))
    assert isinstance(vwap, Decimal)
    assert vwap == Decimal("101.2")


@pytest.mark.asyncio
async def test_get_vwap_returns_none_when_window_has_no_trades(redis_stream_client):
    """Returns None instead of NaN when stream entries contain no trades."""
    stock_id = uuid4()
    analytics = MarketDataAnalytics(redis_stream_client)

    await analytics.record_state_from_order_book(
        stock_id=stock_id,
        bid_levels=[create_price_level(Decimal("100.00"), Decimal(10))],
        ask_levels=[create_price_level(Decimal("101.00"), Decimal(5))],
        last_trade_price=None,
        last_trade_quantity=None,
        trades=[],
    )

    vwap = await analytics.get_vwap(stock_id, window=timedelta(minutes=5))
    assert vwap is None


@pytest.mark.asyncio
async def test_get_vwap_accounts_for_every_fill_in_multi_fill_sweep(
    redis_stream_client,
    mock_kafka_producer: AsyncMock,
):
    """Includes every fill from a multi-level sweep rather than only the last."""
    stock_id = uuid4()
    analytics = MarketDataAnalytics(redis_stream_client)
    engine = MatchingEngine(mock_kafka_producer, analytics)

    for price, qty in (
        (Decimal("100.00"), Decimal(10)),
        (Decimal("104.00"), Decimal(30)),
    ):
        await engine.process_order(
            {
                "id": str(uuid4()),
                "stock_id": str(stock_id),
                "ticker": "AAPL",
                "price": price,
                "quantity": qty,
                "side": OrderSide.SELL,
                "order_type": OrderType.LIMIT,
            }
        )

    await engine.process_order(
        {
            "id": str(uuid4()),
            "stock_id": str(stock_id),
            "ticker": "AAPL",
            "price": Decimal("105.00"),
            "quantity": Decimal(40),
            "side": OrderSide.BUY,
            "order_type": OrderType.LIMIT,
        }
    )

    vwap = await analytics.get_vwap(stock_id, window=timedelta(minutes=5))
    # (100 * 10 + 104 * 30) / 40 = 103 (vs 102 unweighted mean, 104 last fill).
    assert vwap == Decimal(103)


@pytest.mark.asyncio
async def test_get_vwap_ignores_carried_last_trade_when_trades_empty(
    redis_stream_client,
):
    """Passing trades=[] never double-counts a carried-over last_trade_*."""
    stock_id = uuid4()
    analytics = MarketDataAnalytics(redis_stream_client)

    await analytics.record_state_from_order_book(
        stock_id=stock_id,
        bid_levels=[],
        ask_levels=[],
        last_trade_price=Decimal("100.00"),
        last_trade_quantity=Decimal(10),
        trades=[],
    )

    assert await analytics.get_vwap(stock_id, window=timedelta(minutes=5)) is None


@pytest.mark.asyncio
async def test_get_vwap_decodes_bytes_stream_fields(
    redis_stream_client,
    monkeypatch: pytest.MonkeyPatch,
):
    """Handle Redis clients returning bytes keys and values in stream entries."""
    stock_id = uuid4()
    analytics = MarketDataAnalytics(redis_stream_client)

    async def bytes_xrange(key, min, max, **kwargs):
        return [
            (
                b"1-0",
                {
                    b"trade_notional": b"1000.00",
                    b"trade_volume": b"10",
                },
            ),
            (
                b"2-0",
                {
                    b"last_trade_price": b"104.00",
                    b"last_trade_quantity": b"30",
                },
            ),
        ]

    monkeypatch.setattr(redis_stream_client, "xrange", bytes_xrange)

    assert await analytics.get_vwap(stock_id, window=timedelta(minutes=5)) == Decimal(
        103
    )


@pytest.mark.asyncio
async def test_get_vwap_queries_open_upper_stream_bound(
    redis_stream_client,
    monkeypatch: pytest.MonkeyPatch,
):
    """Passes max='+' to xrange so current-millisecond entries are not dropped."""
    stock_id = uuid4()
    analytics = MarketDataAnalytics(redis_stream_client)
    recorded_kwargs: list[dict] = []

    async def spy_xrange(key, min, max, **kwargs):
        recorded_kwargs.append({"key": key, "min": min, "max": max})
        return []

    monkeypatch.setattr(redis_stream_client, "xrange", spy_xrange)

    assert await analytics.get_vwap(stock_id, window=timedelta(minutes=1)) is None
    assert recorded_kwargs and recorded_kwargs[0]["max"] == "+"
