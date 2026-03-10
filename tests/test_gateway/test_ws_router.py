import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from starlette.testclient import TestClient

from order_book_simulator.common.models import OrderSide, OrderType
from order_book_simulator.gateway.app import app


def _mock_stock(stock_id):
    """Creates a mock stock object with the given ID."""
    stock = MagicMock()
    stock.id = stock_id
    stock.ticker = "TICKER"
    return stock


def _patch_db(monkeypatch, stock):
    """Patches AsyncSessionLocal and get_stock_by_ticker."""

    async def mock_get_stock(ticker, db):
        return stock

    monkeypatch.setattr(
        "order_book_simulator.gateway.routers.ws_router.get_stock_by_ticker",
        mock_get_stock,
    )
    monkeypatch.setattr(
        "order_book_simulator.gateway.routers.ws_router.AsyncSessionLocal",
        MagicMock(
            return_value=MagicMock(
                __aenter__=AsyncMock(return_value=MagicMock()),
                __aexit__=AsyncMock(return_value=False),
            )
        ),
    )


# --- Fresh connection (no last_seen_sequence_number) ---


def test_ws_receives_initial_snapshot(matching_engine, monkeypatch):
    """Tests that a WebSocket client receives the initial snapshot on connect."""
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

    _patch_db(monkeypatch, _mock_stock(stock_id))

    client = TestClient(app)
    with client.websocket_connect("/v1/ws/TICKER") as ws:
        data = ws.receive_json()
        assert data["type"] == "snapshot"
        assert "data" in data
        assert "current_sequence_number" in data
        assert data["data"]["bids"][0]["price"] == "100.00"


def test_ws_snapshot_includes_sequence_number(matching_engine, monkeypatch):
    """Tests that the snapshot includes the current sequence number."""
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

    _patch_db(monkeypatch, _mock_stock(stock_id))

    client = TestClient(app)
    with client.websocket_connect("/v1/ws/TICKER") as ws:
        data = ws.receive_json()
        assert data["current_sequence_number"] >= 1


# --- Reconnect with sequence recovery ---


def test_ws_reconnect_receives_missed_deltas(matching_engine, monkeypatch):
    """
    Tests that a reconnecting client with a valid sequence number
    receives only the missed deltas instead of a full snapshot.
    """
    stock_id = uuid4()
    # Process two orders to generate deltas with sequence numbers.
    for price in ("100.00", "101.00"):
        order = {
            "id": str(uuid4()),
            "stock_id": str(stock_id),
            "ticker": "TICKER",
            "price": price,
            "quantity": "10.00",
            "side": OrderSide.BUY.value,
            "order_type": OrderType.LIMIT.value,
            "created_at": datetime.now(timezone.utc),
        }
        asyncio.run(matching_engine.process_order(order))

    _patch_db(monkeypatch, _mock_stock(stock_id))

    client = TestClient(app)
    # First connect to get the current sequence number.
    with client.websocket_connect("/v1/ws/TICKER") as ws:
        snapshot = ws.receive_json()
        seq = snapshot["current_sequence_number"]

    # Process another order to generate a new delta.
    order = {
        "id": str(uuid4()),
        "stock_id": str(stock_id),
        "ticker": "TICKER",
        "price": "102.00",
        "quantity": "10.00",
        "side": OrderSide.BUY.value,
        "order_type": OrderType.LIMIT.value,
        "created_at": datetime.now(timezone.utc),
    }
    asyncio.run(matching_engine.process_order(order))

    # Reconnect with the last seen sequence number.
    with client.websocket_connect(
        f"/v1/ws/TICKER?last_seen_sequence_number={seq}"
    ) as ws:
        data = ws.receive_json()
        assert data["type"] == "deltas"
        assert len(data["data"]) > 0
        assert "current_sequence_number" in data


def test_ws_reconnect_up_to_date_receives_empty_deltas(matching_engine, monkeypatch):
    """
    Tests that a reconnecting client that is already up to date
    receives an empty deltas list.
    """
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

    _patch_db(monkeypatch, _mock_stock(stock_id))

    client = TestClient(app)
    # Get current sequence number.
    with client.websocket_connect("/v1/ws/TICKER") as ws:
        snapshot = ws.receive_json()
        seq = snapshot["current_sequence_number"]

    # Reconnect with the latest sequence number - no new deltas.
    with client.websocket_connect(
        f"/v1/ws/TICKER?last_seen_sequence_number={seq}"
    ) as ws:
        data = ws.receive_json()
        assert data["type"] == "deltas"
        assert data["data"] == []


def test_ws_reconnect_evicted_sequence_falls_back_to_snapshot(
    matching_engine, monkeypatch
):
    """
    Tests that a reconnecting client with an evicted sequence number
    falls back to a full snapshot.
    """
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

    _patch_db(monkeypatch, _mock_stock(stock_id))

    client = TestClient(app)
    # Use sequence 0, which is before any deltas exist. The cache
    # stores deltas starting from sequence 1, so requesting
    # sequence 0 means the client is behind the buffer window.
    # However, get_deltas_since returns all deltas if seq 0 is
    # within range. Use a negative number to force eviction.
    with client.websocket_connect("/v1/ws/TICKER?last_seen_sequence_number=-100") as ws:
        data = ws.receive_json()
        assert data["type"] == "snapshot"
        assert "data" in data
        assert "current_sequence_number" in data
