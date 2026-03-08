import asyncio
from datetime import datetime, timezone
from unittest.mock import MagicMock
from uuid import uuid4

from order_book_simulator.common.models import OrderSide, OrderType


def _mock_stock(stock_id):
    """Creates a mock stock object with the given ID."""
    stock = MagicMock()
    stock.id = stock_id
    stock.ticker = "TICKER"
    return stock


def test_get_deltas_returns_deltas(test_client, matching_engine, monkeypatch):
    """Tests that the deltas endpoint returns incremental deltas."""
    stock_id = uuid4()
    order = {
        "id": str(uuid4()),
        "stock_id": str(stock_id),
        "ticker": "TICKER",
        "price": "100.00",
        "quantity": "10.00",
        "side": OrderSide.BUY.value,
        "order_type": OrderType.LIMIT.value,
        "created_at": datetime.now(timezone.utc),
    }
    asyncio.run(matching_engine.process_order(order))

    # Patch stock lookup to return the same stock_id used above.
    async def mock_get_stock(ticker, db):
        return _mock_stock(stock_id)

    monkeypatch.setattr(
        "order_book_simulator.gateway.routers.order_book_router.get_stock_by_ticker",
        mock_get_stock,
    )

    response = test_client.get(
        "/v1/order-book/TICKER/deltas",
        params={"sequence_number": 0},
    )
    assert response.status_code == 200

    data = response.json()
    assert "deltas" in data
    assert "current_delta_sequence_number" in data
    assert len(data["deltas"]) >= 1
    assert data["deltas"][0]["delta_type"] == "LEVEL_UPDATE"
    assert data["current_delta_sequence_number"] >= 1


def test_get_deltas_returns_empty_when_up_to_date(
    test_client, matching_engine, monkeypatch
):
    """Tests that an empty delta list is returned when client is current."""
    stock_id = uuid4()
    order = {
        "id": str(uuid4()),
        "stock_id": str(stock_id),
        "ticker": "TICKER",
        "price": "100.00",
        "quantity": "10.00",
        "side": OrderSide.BUY.value,
        "order_type": OrderType.LIMIT.value,
        "created_at": datetime.now(timezone.utc),
    }
    asyncio.run(matching_engine.process_order(order))

    async def mock_get_stock(ticker, db):
        return _mock_stock(stock_id)

    monkeypatch.setattr(
        "order_book_simulator.gateway.routers.order_book_router.get_stock_by_ticker",
        mock_get_stock,
    )

    # Request with a sequence number beyond the current - should be
    # up to date.
    response = test_client.get(
        "/v1/order-book/TICKER/deltas",
        params={"sequence_number": 999},
    )
    assert response.status_code == 200

    data = response.json()
    assert "deltas" in data
    assert len(data["deltas"]) == 0


def test_get_deltas_snapshot_fallback(test_client, matching_engine, monkeypatch):
    """
    Tests that a snapshot is returned when the client is too far
    behind.
    """
    stock_id = uuid4()
    # Add multiple orders to push the buffer forward.
    for i in range(3):
        order = {
            "id": str(uuid4()),
            "stock_id": str(stock_id),
            "ticker": "TICKER",
            "price": str(100 + i),
            "quantity": "10.00",
            "side": OrderSide.BUY.value,
            "order_type": OrderType.LIMIT.value,
            "created_at": datetime.now(timezone.utc),
        }
        asyncio.run(matching_engine.process_order(order))

    async def mock_get_stock(ticker, db):
        return _mock_stock(stock_id)

    monkeypatch.setattr(
        "order_book_simulator.gateway.routers.order_book_router.get_stock_by_ticker",
        mock_get_stock,
    )

    # Requesting seq -1 triggers eviction: -1 < oldest_seq - 1.
    response = test_client.get(
        "/v1/order-book/TICKER/deltas",
        params={"sequence_number": -1},
    )
    assert response.status_code == 200

    data = response.json()
    assert "snapshot" in data
    assert "deltas" not in data
    assert "current_delta_sequence_number" in data


def test_get_deltas_nonexistent_stock(test_client, monkeypatch):
    """Tests that requesting deltas for a non-existent stock returns 404."""

    async def mock_get_stock(ticker, db):
        return None

    monkeypatch.setattr(
        "order_book_simulator.gateway.routers.order_book_router.get_stock_by_ticker",
        mock_get_stock,
    )

    response = test_client.get(
        "/v1/order-book/NONEXIST/deltas",
        params={"sequence_number": 0},
    )
    assert response.status_code == 404
