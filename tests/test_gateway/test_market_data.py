from datetime import datetime, timezone
from uuid import uuid4

from order_book_simulator.common.cache import order_book_cache
from order_book_simulator.common.models import OrderSide, OrderType


def test_get_active_stocks_with_orders(test_client, matching_engine, event_loop):
    """Tests getting active stocks with existing order books."""
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
    assert len(data["tickers"]) == 2


def test_get_active_stocks_empty(test_client):
    """Tests getting active stocks when no order books exist."""
    response = test_client.get("/v1/market-data/stocks-with-orders")
    assert response.status_code == 200
    data = response.json()
    assert "timestamp" in data
    assert "tickers" in data
    assert len(data["tickers"]) == 0


def test_get_all_order_books_with_orders(test_client, matching_engine, event_loop):
    """Tests getting all order books with existing orders."""
    # Create orders for two different stocks
    stock_ids = [uuid4(), uuid4()]
    for stock_id in stock_ids:
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

    # Get all order books
    response = test_client.get("/v1/order-book/collection")
    assert response.status_code == 200

    data = response.json()
    assert "timestamp" in data
    assert "order_books" in data
    assert len(data["order_books"]) == 2

    # Verify order books are sorted by stock ID
    received_ids = list(data["order_books"].keys())
    assert received_ids == sorted(received_ids)

    # Check each order book has the expected structure
    for stock_id in stock_ids:
        stock_id_str = str(stock_id)
        assert stock_id_str in data["order_books"]
        order_book = data["order_books"][stock_id_str]
        assert "bids" in order_book
        assert "asks" in order_book
        assert len(order_book["bids"]) == 1  # Should have our buy order.
        assert len(order_book["asks"]) == 0  # No sell orders.
        assert "trades" in order_book
        assert len(order_book["trades"]) == 0  # Only buy orders, so no trades.


def test_get_order_book_for_nonexistent_stock(test_client):
    """Tests getting an order book for a stock that doesn't exist in the database."""
    response = test_client.get("/v1/order-book/non_existent_stock")
    # Should fail because the stock doesn't exist in the database.
    assert response.status_code == 404


def test_get_all_order_books_empty(test_client):
    """Tests getting all order books when none exist."""
    response = test_client.get("v1/order-book/collection")
    assert response.status_code == 200
    data = response.json()
    assert "timestamp" in data
    assert "order_books" in data
    assert len(data["order_books"]) == 0
