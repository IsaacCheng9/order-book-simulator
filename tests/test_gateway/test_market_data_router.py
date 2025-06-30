from datetime import datetime, timezone
from uuid import uuid4

from order_book_simulator.common.cache import order_book_cache
from order_book_simulator.common.models import OrderSide, OrderType


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
