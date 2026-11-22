import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import MagicMock
from uuid import uuid4

from order_book_simulator.common.cache import order_book_cache
from order_book_simulator.common.models import (
    Delta,
    DeltaType,
    OrderSide,
    OrderType,
)


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
        "created_at": datetime.now(UTC),
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
        "created_at": datetime.now(UTC),
    }
    asyncio.run(matching_engine.process_order(order))

    async def mock_get_stock(ticker, db):
        return _mock_stock(stock_id)

    monkeypatch.setattr(
        "order_book_simulator.gateway.routers.order_book_router.get_stock_by_ticker",
        mock_get_stock,
    )

    # Request at the exact current sequence number - client is up to date.
    response = test_client.get(
        "/v1/order-book/TICKER/deltas",
        params={"sequence_number": 1},
    )
    assert response.status_code == 200

    data = response.json()
    assert data["deltas"] == []
    assert data["current_delta_sequence_number"] == 1

    # Request ahead of server sequence (e.g. after Redis reset) falls back to
    # an atomic snapshot + cursor.
    ahead_response = test_client.get(
        "/v1/order-book/TICKER/deltas",
        params={"sequence_number": 999},
    )
    assert ahead_response.status_code == 200
    ahead_data = ahead_response.json()
    assert "snapshot" in ahead_data
    assert ahead_data["current_delta_sequence_number"] == 1

    # GET /{ticker} also returns the atomic snapshot cursor.
    book_response = test_client.get("/v1/order-book/TICKER")
    assert book_response.status_code == 200
    assert book_response.json()["current_delta_sequence_number"] == 1


def test_get_deltas_snapshot_fallback(test_client, monkeypatch):
    """Return a snapshot when the client's sequence has been evicted."""
    stock_id = uuid4()
    snapshot = {
        "bids": [{"price": "100.00", "quantity": "10.00"}],
        "asks": [],
    }
    evicted_window = [
        Delta(
            sequence_number=seq,
            timestamp=1000.0 + seq,
            delta_type=DeltaType.LEVEL_UPDATE,
            ticker="TICKER",
            side=OrderSide.BUY,
            price=Decimal(100),
            quantity=Decimal(10),
            order_count=1,
        )
        for seq in (5, 6, 7)
    ]
    asyncio.run(
        order_book_cache.store_order_book_and_deltas(stock_id, snapshot, evicted_window)
    )

    async def mock_get_stock(ticker, db):
        return _mock_stock(stock_id)

    monkeypatch.setattr(
        "order_book_simulator.gateway.routers.order_book_router.get_stock_by_ticker",
        mock_get_stock,
    )

    # Sequence 0 predates the retained window [5, 7], triggering fallback.
    response = test_client.get(
        "/v1/order-book/TICKER/deltas",
        params={"sequence_number": 0},
    )
    assert response.status_code == 200

    data = response.json()
    assert data["snapshot"] == snapshot
    assert "deltas" not in data
    assert data["current_delta_sequence_number"] == 7


def test_get_deltas_rejects_negative_sequence_number(test_client):
    """Reject negative sequence numbers with HTTP 422."""
    response = test_client.get(
        "/v1/order-book/TICKER/deltas",
        params={"sequence_number": -1},
    )
    assert response.status_code == 422


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
