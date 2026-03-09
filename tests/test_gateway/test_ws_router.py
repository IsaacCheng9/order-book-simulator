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

    mock_stock = _mock_stock(stock_id)

    # Patch AsyncSessionLocal to return a mock session, and
    # get_stock_by_ticker to return our known stock.
    async def mock_get_stock(ticker, db):
        return mock_stock

    monkeypatch.setattr(
        "order_book_simulator.gateway.routers.ws_router.get_stock_by_ticker",
        mock_get_stock,
    )
    monkeypatch.setattr(
        "order_book_simulator.gateway.routers.ws_router.AsyncSessionLocal",
        MagicMock(return_value=MagicMock(
            __aenter__=AsyncMock(return_value=MagicMock()),
            __aexit__=AsyncMock(return_value=False),
        )),
    )

    client = TestClient(app)
    with client.websocket_connect("/v1/ws/TICKER") as ws:
        data = ws.receive_json()
        assert data["type"] == "snapshot"
        assert "data" in data
        assert "current_sequence_number" in data
        assert data["data"]["bids"][0]["price"] == "100.00"


def test_ws_snapshot_includes_sequence_number(
    matching_engine, monkeypatch
):
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

    async def mock_get_stock(ticker, db):
        return _mock_stock(stock_id)

    monkeypatch.setattr(
        "order_book_simulator.gateway.routers.ws_router.get_stock_by_ticker",
        mock_get_stock,
    )
    monkeypatch.setattr(
        "order_book_simulator.gateway.routers.ws_router.AsyncSessionLocal",
        MagicMock(return_value=MagicMock(
            __aenter__=AsyncMock(return_value=MagicMock()),
            __aexit__=AsyncMock(return_value=False),
        )),
    )

    client = TestClient(app)
    with client.websocket_connect("/v1/ws/TICKER") as ws:
        data = ws.receive_json()
        assert data["current_sequence_number"] >= 1
