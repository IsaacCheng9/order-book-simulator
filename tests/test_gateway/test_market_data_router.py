import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from order_book_simulator.common.cache import order_book_cache
from order_book_simulator.common.models import OrderSide, OrderType
from order_book_simulator.database.queries import (
    get_global_trade_analytics,
    get_trade_analytics_by_stock,
)
from order_book_simulator.gateway.routers import market_data_router
from order_book_simulator.market_data.analytics import MarketDataAnalytics


def _assert_trade_structure(trade: dict) -> None:
    """Helper function to assert trade structure."""
    required_fields = [
        "id",
        "stock_id",
        "price",
        "quantity",
        "total_amount",
        "trade_time",
    ]
    for field in required_fields:
        assert field in trade


@pytest.mark.asyncio
async def test_get_active_stocks_with_orders(test_client, matching_engine):
    """Tests getting active stocks with existing order books."""
    # Use predefined stock IDs that work with the mock
    stock_ids = [uuid4(), uuid4()]

    for stock_id in stock_ids:
        # Initialise empty order book
        await order_book_cache.set_order_book(stock_id, {"bids": [], "asks": []})

        # Create and process test order
        order = {
            "id": str(uuid4()),
            "stock_id": str(stock_id),
            "ticker": "TICKER",
            "price": "100.00",
            "quantity": "10.00",
            "side": OrderSide.BUY.value,
            "order_type": OrderType.LIMIT.value,
            "created_at": datetime.now(UTC),
        }
        await matching_engine.process_order(order)

    response = test_client.get("/v1/market-data/stocks-with-orders")
    assert response.status_code == 200
    data = response.json()
    assert "timestamp" in data
    assert "tickers" in data
    assert "stock_id_to_ticker" in data
    assert len(data["tickers"]) == 2
    assert len(data["stock_id_to_ticker"]) == 2

    # The mock creates tickers like "STOCK_{id}" based on the conftest.py mock
    expected_tickers = [f"STOCK_{stock_id}" for stock_id in stock_ids]
    for ticker in expected_tickers:
        assert ticker in data["tickers"]

    # Verify stock_id to ticker mapping
    stock_id_to_ticker = data["stock_id_to_ticker"]
    for stock_id in stock_ids:
        assert str(stock_id) in stock_id_to_ticker
        assert stock_id_to_ticker[str(stock_id)] == f"STOCK_{stock_id}"


def test_get_active_stocks_empty(test_client):
    """Tests getting active stocks when no order books exist."""
    response = test_client.get("/v1/market-data/stocks-with-orders")
    assert response.status_code == 200
    data = response.json()
    assert "timestamp" in data
    assert "tickers" in data
    assert "stock_id_to_ticker" in data
    assert len(data["tickers"]) == 0
    assert len(data["stock_id_to_ticker"]) == 0


def test_get_all_stocks(test_client):
    """Tests getting all stocks from the database."""
    response = test_client.get("/v1/market-data/stocks")
    assert response.status_code == 200
    data = response.json()

    # Check response structure
    assert "timestamp" in data
    assert "stocks" in data

    # Check mock data is returned
    stocks = data["stocks"]
    assert len(stocks) == 4

    # Verify expected stocks are present (from conftest.py mock)
    expected_stocks = [
        {"ticker": "AAPL", "company_name": "Apple Inc."},
        {"ticker": "GOOGL", "company_name": "Alphabet Inc."},
        {"ticker": "MSFT", "company_name": "Microsoft Corporation"},
        {"ticker": "TSLA", "company_name": "Tesla Inc."},
    ]

    for expected_stock in expected_stocks:
        assert expected_stock in stocks

    # Verify stocks are sorted by ticker (from ORDER BY in query)
    tickers = [stock["ticker"] for stock in stocks]
    assert tickers == sorted(tickers)


def test_get_all_recent_trades_structure(test_client):
    """Tests the structure of the recent trades response."""
    response = test_client.get("/v1/market-data/trades")
    assert response.status_code == 200
    data = response.json()

    # Check response structure
    assert "timestamp" in data
    assert "trades" in data
    assert "count" in data

    # Verify the count matches the trades length
    assert data["count"] == len(data["trades"])

    # If trades exist, verify their structure
    if data["trades"]:
        for trade in data["trades"]:
            _assert_trade_structure(trade)
            assert "ticker" in trade


def test_get_all_recent_trades_with_limit(test_client):
    """Tests getting recent trades with custom limit."""
    response = test_client.get("/v1/market-data/trades?limit=3")
    assert response.status_code == 200
    data = response.json()

    assert "timestamp" in data
    assert "trades" in data
    assert "count" in data
    assert data["count"] == 3

    # Verify trade structure from mocked data
    for trade in data["trades"]:
        _assert_trade_structure(trade)
        assert "ticker" in trade


def test_get_stock_trades_success(test_client):
    """Tests getting trades for a specific stock successfully."""
    ticker = "AAPL"

    response = test_client.get(f"/v1/market-data/trades/{ticker}")
    assert response.status_code == 200
    data = response.json()

    assert "timestamp" in data
    assert "ticker" in data
    assert "trades" in data
    assert "count" in data
    assert data["ticker"] == ticker
    assert data["count"] == 2  # From mock data in conftest.py

    # Verify trade structure
    for trade in data["trades"]:
        _assert_trade_structure(trade)


def test_get_stock_trade_analytics_success(test_client):
    """Tests getting trade analytics for a specific stock successfully."""
    ticker = "AAPL"

    response = test_client.get(f"/v1/market-data/trades/{ticker}/analytics")
    assert response.status_code == 200
    data = response.json()

    assert "timestamp" in data
    assert "ticker" in data
    assert "period_hours" in data
    assert "analytics" in data
    assert data["ticker"] == ticker
    assert data["period_hours"] == 24  # Default value

    analytics = data["analytics"]
    assert analytics["trade_count"] == 10
    assert analytics["total_volume"] == "1000.0"
    assert analytics["avg_price"] == "150.0"
    assert analytics["min_price"] == "145.0"
    assert analytics["max_price"] == "155.0"
    assert "vwap" in analytics


def test_get_stock_trade_analytics_custom_period(test_client):
    """Tests getting trade analytics with custom time period."""
    ticker = "AAPL"

    response = test_client.get(
        f"/v1/market-data/trades/{ticker}/analytics?since_hours=12"
    )
    assert response.status_code == 200
    data = response.json()

    assert data["ticker"] == ticker
    assert data["period_hours"] == 12
    assert data["analytics"]["trade_count"] == 10  # From mock data


def test_get_global_trade_analytics_success(test_client):
    """Tests getting global trade analytics successfully."""
    response = test_client.get("/v1/market-data/global-trades-analytics")
    assert response.status_code == 200
    data = response.json()

    assert "timestamp" in data
    assert "period_hours" in data
    assert "analytics" in data
    assert data["period_hours"] == 24  # Default value

    # From mock data (global)
    analytics = data["analytics"]
    assert analytics["trade_count"] == 50
    assert analytics["total_volume"] == "5000.0"
    assert analytics["total_value"] == "750000.0"
    assert analytics["avg_quantity"] == "100.0"
    assert analytics["avg_price"] == "150.0"
    assert analytics["min_price"] == "100.0"
    assert analytics["max_price"] == "200.0"
    assert "vwap" in analytics


def test_get_global_trade_analytics_custom_period(test_client):
    """Tests getting global trade analytics with custom time period."""
    response = test_client.get("/v1/market-data/global-trades-analytics?since_hours=12")
    assert response.status_code == 200
    data = response.json()

    assert data["period_hours"] == 12
    # From mock data
    assert data["analytics"]["trade_count"] == 50


@pytest.mark.asyncio
async def test_trade_analytics_null_handling():
    """
    Tests that our NULL handling logic correctly handles SQL aggregate
    NULL values.

    When no trades exist, SQL aggregate functions return:
    - COUNT: 0
    - SUM/AVG/MIN/MAX: NULL (Python None)
    """

    # Create a mock database session
    mock_db = AsyncMock()

    # Mock execute to return a row with NULL aggregate values (like
    # real SQL would).
    mock_result = MagicMock()
    mock_row = MagicMock()
    mock_row.trade_count = 0  # COUNT returns 0
    mock_row.total_volume = None  # SUM returns NULL
    mock_row.total_value = None  # SUM returns NULL
    mock_row.avg_price = None  # AVG returns NULL
    mock_row.avg_quantity = None  # AVG returns NULL
    mock_row.min_price = None  # MIN returns NULL
    mock_row.max_price = None  # MAX returns NULL
    mock_result.first.return_value = mock_row
    mock_db.execute.return_value = mock_result

    # Test stock analytics - should raise exception when trade_count = 0
    with pytest.raises(Exception, match="No trades found for stock"):
        await get_trade_analytics_by_stock(uuid4(), mock_db)

    # Test global analytics - should return empty analytics when
    # trade_count = 0
    result = await get_global_trade_analytics(mock_db)
    assert result["trade_count"] == 0
    assert result["total_volume"] == "0"
    assert result["total_value"] == "0"
    assert result["avg_quantity"] is None
    assert result["avg_price"] is None
    assert result["min_price"] is None
    assert result["max_price"] is None
    assert result["vwap"] is None


def test_get_market_data(test_client, monkeypatch):
    """Tests getting market data for a specific stock."""
    ticker = "AAPL"

    mock_analytics = MagicMock()
    mock_analytics.get_market_depth = AsyncMock(
        return_value={
            "bid_depth": 1000.0,
            "ask_depth": 1500.0,
            "spread": 0.5,
            "mid_price": 150.25,
        }
    )
    mock_analytics.get_vwap = AsyncMock(return_value=150.10)
    monkeypatch.setattr(market_data_router, "analytics", mock_analytics)

    response = test_client.get(f"/v1/market-data/{ticker}")
    assert response.status_code == 200
    data = response.json()

    assert "timestamp" in data
    assert "ticker" in data
    assert data["ticker"] == ticker
    assert "bid_depth" in data
    assert "ask_depth" in data


def test_get_market_data_returns_null_vwap_when_no_trades(
    test_client, monkeypatch, redis_stream_client
):
    """Returns vwap_1min as null rather than 'nan' when no trades occurred."""
    real_analytics = MarketDataAnalytics(redis_stream_client)
    monkeypatch.setattr(market_data_router, "analytics", real_analytics)

    stock_id = uuid4()

    async def mock_get_stock(ticker, db):
        stock = MagicMock()
        stock.id = stock_id
        stock.ticker = ticker.upper()
        return stock

    monkeypatch.setattr(market_data_router, "get_stock_by_ticker", mock_get_stock)

    asyncio.run(
        real_analytics.record_state_from_order_book(
            stock_id=stock_id,
            bid_levels=[],
            ask_levels=[],
            last_trade_price=None,
            last_trade_quantity=None,
        )
    )

    response = test_client.get("/v1/market-data/AAPL")
    assert response.status_code == 200
    assert response.json()["vwap_1min"] is None


def test_get_market_data_serialises_decimal_vwap(
    test_client, monkeypatch, redis_stream_client
):
    """Serialises non-null Decimal VWAP accurately through /v1/market-data."""
    real_analytics = MarketDataAnalytics(redis_stream_client)
    monkeypatch.setattr(market_data_router, "analytics", real_analytics)

    stock_id = uuid4()

    async def mock_get_stock(ticker, db):
        stock = MagicMock()
        stock.id = stock_id
        stock.ticker = ticker.upper()
        return stock

    monkeypatch.setattr(market_data_router, "get_stock_by_ticker", mock_get_stock)

    asyncio.run(
        real_analytics.record_state_from_order_book(
            stock_id=stock_id,
            bid_levels=[],
            ask_levels=[],
            last_trade_price=None,
            last_trade_quantity=None,
            trades=[
                {"price": Decimal("100.00"), "quantity": Decimal(10)},
                {"price": Decimal("104.00"), "quantity": Decimal(30)},
            ],
        )
    )

    response = test_client.get("/v1/market-data/AAPL")
    assert response.status_code == 200
    assert Decimal(response.json()["vwap_1min"]) == Decimal(103)


@pytest.mark.asyncio
async def test_sql_trade_analytics_vwap_uses_decimal_precision():
    """Verify SQL analytics VWAP preserves Decimal precision without float drift."""
    stock_id = uuid4()
    mock_db = AsyncMock()
    mock_result = MagicMock()
    mock_row = MagicMock()
    mock_row.trade_count = 2
    mock_row.total_volume = Decimal(3)
    mock_row.total_value = Decimal("0.30")
    mock_row.avg_price = Decimal("0.10")
    mock_row.avg_quantity = Decimal("1.5")
    mock_row.min_price = Decimal("0.10")
    mock_row.max_price = Decimal("0.10")
    mock_result.first.return_value = mock_row
    mock_db.execute.return_value = mock_result

    stock_stats = await get_trade_analytics_by_stock(stock_id, mock_db)
    global_stats = await get_global_trade_analytics(mock_db)
    assert stock_stats["vwap"] == "0.10"
    assert global_stats["vwap"] == "0.10"


@pytest.mark.parametrize("limit", [0, -1, 1001])
@pytest.mark.parametrize(
    "path", ["/v1/market-data/trades", "/v1/market-data/trades/AAPL"]
)
def test_trades_endpoints_reject_out_of_bounds_limit(test_client, path, limit):
    """Reject non-positive or oversized limit values with HTTP 422."""
    response = test_client.get(path, params={"limit": limit})
    assert response.status_code == 422


@pytest.mark.parametrize("limit", [1, 1000])
@pytest.mark.parametrize(
    "path", ["/v1/market-data/trades", "/v1/market-data/trades/AAPL"]
)
def test_trades_endpoints_accept_boundary_limit(test_client, path, limit):
    """Accept inclusive boundary values for the trades limit parameter."""
    response = test_client.get(path, params={"limit": limit})
    assert response.status_code == 200


@pytest.mark.parametrize("since_hours", [0, -5, 87601, 10**12])
@pytest.mark.parametrize(
    "path",
    [
        "/v1/market-data/global-trades-analytics",
        "/v1/market-data/trades/AAPL/analytics",
    ],
)
def test_analytics_endpoints_reject_out_of_bounds_since_hours(
    test_client, path, since_hours
):
    """Reject non-positive or overflow-prone since_hours values with 422."""
    response = test_client.get(path, params={"since_hours": since_hours})
    assert response.status_code == 422


@pytest.mark.parametrize("since_hours", [1, 8760, 87600])
@pytest.mark.parametrize(
    "path",
    [
        "/v1/market-data/global-trades-analytics",
        "/v1/market-data/trades/AAPL/analytics",
    ],
)
def test_analytics_endpoints_accept_boundary_since_hours(
    test_client, path, since_hours
):
    """Accept valid UI and boundary values for since_hours."""
    response = test_client.get(path, params={"since_hours": since_hours})
    assert response.status_code == 200
    assert response.json()["period_hours"] == since_hours
