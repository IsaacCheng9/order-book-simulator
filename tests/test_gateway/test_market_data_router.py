from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from order_book_simulator.common.cache import order_book_cache
from order_book_simulator.common.models import OrderSide, OrderType


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


def test_get_active_stocks_with_orders(test_client, matching_engine, event_loop):
    """Tests getting active stocks with existing order books."""
    # Use predefined stock IDs that work with the mock
    stock_ids = [uuid4(), uuid4()]

    for stock_id in stock_ids:
        # Initialise empty order book
        order_book_cache.set_order_book(stock_id, {"bids": [], "asks": []})

        # Create and process test order
        order = {
            "id": str(uuid4()),
            "stock_id": str(stock_id),
            "price": "100.00",
            "quantity": "10.00",
            "side": OrderSide.BUY.value,
            "type": OrderType.LIMIT.value,
            "created_at": datetime.now(timezone.utc),
        }
        event_loop.run_until_complete(matching_engine.process_order(order))

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


def test_get_market_data(test_client):
    """Tests getting market data for a specific stock."""
    ticker = "AAPL"

    # Mock the analytics methods that would be called
    with patch(
        "order_book_simulator.gateway.routers.market_data_router.analytics"
    ) as mock_analytics:
        mock_analytics.get_market_depth = AsyncMock(
            return_value={
                "bid_depth": 1000.0,
                "ask_depth": 1500.0,
                "spread": 0.5,
                "mid_price": 150.25,
            }
        )
        mock_analytics.get_vwap = AsyncMock(return_value=150.10)

        response = test_client.get(f"/v1/market-data/{ticker}")
        assert response.status_code == 200
        data = response.json()

        assert "timestamp" in data
        assert "ticker" in data
        assert data["ticker"] == ticker
        assert "bid_depth" in data
        assert "ask_depth" in data
