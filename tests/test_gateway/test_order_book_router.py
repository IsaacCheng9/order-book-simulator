import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from order_book_simulator.common.models import OrderSide, OrderType
from order_book_simulator.gateway.app_state import app_state
from order_book_simulator.gateway.routers import order_book_router as router_module


def test_get_all_order_books_with_orders(test_client, matching_engine):
    """Tests getting all order books with existing orders."""
    # Create orders for two different stocks
    stock_ids = [uuid4(), uuid4()]
    for stock_id in stock_ids:
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
        asyncio.run(matching_engine.process_order(order))

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
    response = test_client.get("/v1/order-book/collection")
    assert response.status_code == 200
    data = response.json()
    assert "timestamp" in data
    assert "order_books" in data
    assert len(data["order_books"]) == 0


def test_cancel_order_derives_canonical_routing_from_stock(test_client, monkeypatch):
    """Routes cancellations using canonical stock.id and stock.ticker."""
    stock = SimpleNamespace(id=uuid4(), ticker="AAPL")
    order_id = uuid4()
    producer = AsyncMock()
    monkeypatch.setattr(app_state, "producer", producer)

    async def mock_get_stock(ticker, db):
        assert ticker == "aapl"
        return stock

    monkeypatch.setattr(router_module, "get_stock_by_ticker", mock_get_stock)

    response = test_client.delete(
        f"/v1/order-book/orders/{order_id}",
        params={"ticker": "aapl"},
    )

    assert response.status_code == 200
    producer.cancel_order.assert_awaited_once_with(order_id, stock.id, "AAPL")


def test_cancel_order_rejects_mismatched_stock_id(test_client, monkeypatch):
    """Rejects cancellations when client stock_id disagrees with ticker."""
    stock = SimpleNamespace(id=uuid4(), ticker="AAPL")
    producer = AsyncMock()
    monkeypatch.setattr(app_state, "producer", producer)

    async def mock_get_stock(ticker, db):
        return stock

    monkeypatch.setattr(router_module, "get_stock_by_ticker", mock_get_stock)

    response = test_client.delete(
        f"/v1/order-book/orders/{uuid4()}",
        params={"ticker": "AAPL", "stock_id": str(uuid4())},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "stock_id does not match ticker AAPL"
    producer.cancel_order.assert_not_awaited()


def test_cancel_order_rejects_unknown_ticker(test_client, monkeypatch):
    """Returns 404 when cancelling an order for an unknown ticker."""
    producer = AsyncMock()
    monkeypatch.setattr(app_state, "producer", producer)

    async def mock_get_stock(ticker, db):
        return None

    monkeypatch.setattr(router_module, "get_stock_by_ticker", mock_get_stock)

    response = test_client.delete(
        f"/v1/order-book/orders/{uuid4()}",
        params={"ticker": "NOPE"},
    )

    assert response.status_code == 404
    producer.cancel_order.assert_not_awaited()
