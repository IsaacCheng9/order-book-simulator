from datetime import datetime, timezone
from uuid import uuid4

from order_book_simulator.common.models import OrderSide, OrderType


def test_health_check(test_client):
    """Verifies the health check endpoint returns the expected response."""
    response = test_client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"
    assert "timestamp" in response.json()


def test_get_order_book_for_instrument_empty(test_client, matching_engine, event_loop):
    """Tests getting an empty order book."""
    instrument_id = uuid4()

    # Create an order and process it directly through the matching engine
    order = {
        "id": str(uuid4()),
        "instrument_id": str(instrument_id),
        "price": "100.00",
        "quantity": "10.00",
        "side": OrderSide.BUY.value,
        "type": OrderType.LIMIT.value,
        "created_at": datetime.now(timezone.utc),
    }

    # Process the order to create the order book.
    event_loop.run_until_complete(matching_engine.process_order(order))
    # Get the order book.
    response = test_client.get(f"/order-book/{instrument_id}")

    assert response.status_code == 200
    data = response.json()
    assert data["instrument_id"] == str(instrument_id)
    assert "timestamp" in data
    assert "book" in data
    assert "bids" in data["book"]
    assert "asks" in data["book"]
    # We should have our bid and no asks.
    assert len(data["book"]["bids"]) == 1
    assert len(data["book"]["asks"]) == 0


def test_get_nonexistent_order_book(test_client):
    """Tests getting an order book that doesn't exist."""
    instrument_id = uuid4()
    response = test_client.get(f"/order-book/{instrument_id}")
    assert response.status_code == 404


def test_get_all_order_books_empty(test_client, matching_engine):
    """Tests getting all order books when none exist."""
    response = test_client.get("/order-books")
    assert response.status_code == 200
    data = response.json()
    assert "timestamp" in data
    assert "order_books" in data
    assert len(data["order_books"]) == 0


def test_get_all_order_books_with_orders(test_client, matching_engine, event_loop):
    """Tests getting all order books with existing orders."""
    # Create orders for two different instruments (in reverse order)
    instrument_ids = sorted([uuid4(), uuid4()], reverse=True)
    for instrument_id in instrument_ids:
        order = {
            "id": str(uuid4()),
            "instrument_id": str(instrument_id),
            "price": "100.00",
            "quantity": "10.00",
            "side": OrderSide.BUY.value,
            "type": OrderType.LIMIT.value,
            "created_at": datetime.now(timezone.utc),
        }
        event_loop.run_until_complete(matching_engine.process_order(order))

    # Get all order books
    response = test_client.get("/order-books")
    assert response.status_code == 200

    data = response.json()
    assert "timestamp" in data
    assert "order_books" in data
    assert len(data["order_books"]) == 2

    # Verify order books are sorted by instrument ID
    received_ids = list(data["order_books"].keys())
    assert received_ids == sorted(received_ids)

    # Check each order book has the expected structure
    for instrument_id in instrument_ids:
        instrument_id_str = str(instrument_id)
        assert instrument_id_str in data["order_books"]
        book = data["order_books"][instrument_id_str]
        assert "bids" in book
        assert "asks" in book
        assert len(book["bids"]) == 1  # Should have our buy order
        assert len(book["asks"]) == 0  # No sell orders
