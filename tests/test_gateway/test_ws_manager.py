import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from order_book_simulator.gateway import ws_manager as ws_manager_module
from order_book_simulator.gateway.ws_manager import (
    ClientConnection,
    WebSocketConnectionManager,
)


@pytest.fixture
def manager():
    """Creates a fresh WebSocket connection manager."""
    return WebSocketConnectionManager()


@pytest.fixture
def mock_client_cls(monkeypatch):
    """Patches ClientConnection to prevent real task creation."""
    mock_cls = MagicMock()
    # Ensure instances have an awaitable close method.
    mock_cls.return_value.close = AsyncMock()
    monkeypatch.setattr(ws_manager_module, "ClientConnection", mock_cls)
    return mock_cls


def _mock_ws() -> AsyncMock:
    """Creates a mock WebSocket with an async send_json."""
    ws = AsyncMock()
    ws.send_json = AsyncMock()
    return ws


# --- Subscribe / unsubscribe / count ---


def test_subscribe_adds_connection(mock_client_cls, manager):
    """Tests that subscribing adds the connection to the ticker."""
    ws = MagicMock()
    manager.subscribe(ws, "AAPL")

    assert manager.get_connection_count("AAPL") == 1


def test_subscribe_multiple_connections(mock_client_cls, manager):
    """Tests subscribing multiple connections to the same ticker."""
    ws1 = MagicMock()
    ws2 = MagicMock()
    manager.subscribe(ws1, "AAPL")
    manager.subscribe(ws2, "AAPL")

    assert manager.get_connection_count("AAPL") == 2


def test_subscribe_different_tickers(mock_client_cls, manager):
    """Tests subscribing to different tickers independently."""
    ws1 = MagicMock()
    ws2 = MagicMock()
    manager.subscribe(ws1, "AAPL")
    manager.subscribe(ws2, "GOOGL")

    assert manager.get_connection_count("AAPL") == 1
    assert manager.get_connection_count("GOOGL") == 1


@pytest.mark.asyncio
async def test_unsubscribe_removes_connection(mock_client_cls, manager):
    """Tests that unsubscribing removes the connection."""
    ws = MagicMock()
    manager.subscribe(ws, "AAPL")
    await manager.unsubscribe(ws, "AAPL")

    assert manager.get_connection_count("AAPL") == 0


@pytest.mark.asyncio
async def test_unsubscribe_cleans_up_empty_ticker(mock_client_cls, manager):
    """Tests that empty ticker entries are removed from the dict."""
    ws = MagicMock()
    manager.subscribe(ws, "AAPL")
    await manager.unsubscribe(ws, "AAPL")

    assert "AAPL" not in manager.connections_per_ticker


@pytest.mark.asyncio
async def test_unsubscribe_preserves_other_connections(mock_client_cls, manager):
    """Tests that unsubscribing one connection doesn't affect others."""
    ws1 = MagicMock()
    ws2 = MagicMock()
    manager.subscribe(ws1, "AAPL")
    manager.subscribe(ws2, "AAPL")
    await manager.unsubscribe(ws1, "AAPL")

    assert manager.get_connection_count("AAPL") == 1


@pytest.mark.asyncio
async def test_unsubscribe_cancels_sender_task(mock_client_cls, manager):
    """Tests that unsubscribing cancels the client's sender task."""
    ws = MagicMock()
    manager.subscribe(ws, "AAPL")
    client = manager.connections_per_ticker["AAPL"][ws]
    await manager.unsubscribe(ws, "AAPL")

    client.close.assert_called_once()


# --- Broadcast ---


def test_broadcast_enqueues_to_all_subscribers(manager):
    """Tests that broadcast enqueues the message to all subscribers."""
    client1 = MagicMock()
    client2 = MagicMock()
    manager.connections_per_ticker["AAPL"] = {
        MagicMock(): client1,
        MagicMock(): client2,
    }

    message = {"type": "deltas", "data": []}
    manager.broadcast(message, "AAPL")

    client1.enqueue.assert_called_once_with(message)
    client2.enqueue.assert_called_once_with(message)


def test_broadcast_does_not_send_to_other_tickers(manager):
    """Tests that broadcast only sends to the target ticker."""
    client_aapl = MagicMock()
    client_googl = MagicMock()
    manager.connections_per_ticker["AAPL"] = {
        MagicMock(): client_aapl,
    }
    manager.connections_per_ticker["GOOGL"] = {
        MagicMock(): client_googl,
    }

    manager.broadcast({"type": "deltas"}, "AAPL")

    client_aapl.enqueue.assert_called_once()
    client_googl.enqueue.assert_not_called()


def test_get_connection_count_unknown_ticker(manager):
    """Tests that an unknown ticker returns 0 without side effects."""
    assert manager.get_connection_count("UNKNOWN") == 0
    assert "UNKNOWN" not in manager.connections_per_ticker


# --- ClientConnection ---


@pytest.mark.asyncio
async def test_client_connection_enqueue_and_send():
    """Tests that enqueued messages are sent via the sender task."""
    ws = _mock_ws()
    client = ClientConnection(ws)

    message = {"type": "deltas", "data": []}
    assert client.enqueue(message) is True

    # Let the sender task process the message.
    await asyncio.sleep(0.01)

    ws.send_json.assert_called_once_with(message)
    await client.close()


@pytest.mark.asyncio
async def test_client_connection_enqueue_full_queue():
    """Tests that enqueue returns False when the queue is full."""
    ws = _mock_ws()
    # Pause send_json so the queue fills up.
    ws.send_json = AsyncMock(side_effect=lambda _: asyncio.sleep(10))
    client = ClientConnection(ws, max_queue_size=2)

    assert client.enqueue({"seq": 1}) is True
    assert client.enqueue({"seq": 2}) is True
    # Queue is now full.
    assert client.enqueue({"seq": 3}) is False

    await client.close()


@pytest.mark.asyncio
async def test_client_connection_send_loop_exits_on_dead_ws():
    """Tests that the sender task exits when send_json raises."""
    ws = _mock_ws()
    ws.send_json.side_effect = Exception("connection closed")
    client = ClientConnection(ws)

    client.enqueue({"type": "deltas"})
    # Wait for the sender task to exit.
    await asyncio.sleep(0.01)
    assert client.sender_task.done()

    await client.close()
