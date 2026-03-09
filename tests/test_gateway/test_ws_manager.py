from unittest.mock import AsyncMock, MagicMock

import pytest

from order_book_simulator.gateway.ws_manager import WebSocketConnectionManager


@pytest.fixture
def manager():
    """Creates a fresh WebSocket connection manager."""
    return WebSocketConnectionManager()


def test_subscribe_adds_connection(manager):
    """Tests that subscribing adds the connection to the ticker set."""
    ws = MagicMock()
    manager.subscribe(ws, "AAPL")

    assert manager.get_connection_count("AAPL") == 1


def test_subscribe_multiple_connections(manager):
    """Tests subscribing multiple connections to the same ticker."""
    ws1 = MagicMock()
    ws2 = MagicMock()
    manager.subscribe(ws1, "AAPL")
    manager.subscribe(ws2, "AAPL")

    assert manager.get_connection_count("AAPL") == 2


def test_subscribe_different_tickers(manager):
    """Tests subscribing to different tickers independently."""
    ws1 = MagicMock()
    ws2 = MagicMock()
    manager.subscribe(ws1, "AAPL")
    manager.subscribe(ws2, "GOOGL")

    assert manager.get_connection_count("AAPL") == 1
    assert manager.get_connection_count("GOOGL") == 1


def test_unsubscribe_removes_connection(manager):
    """Tests that unsubscribing removes the connection."""
    ws = MagicMock()
    manager.subscribe(ws, "AAPL")
    manager.unsubscribe(ws, "AAPL")

    assert manager.get_connection_count("AAPL") == 0


def test_unsubscribe_cleans_up_empty_ticker(manager):
    """Tests that empty ticker entries are removed from the dict."""
    ws = MagicMock()
    manager.subscribe(ws, "AAPL")
    manager.unsubscribe(ws, "AAPL")

    assert "AAPL" not in manager.connections_per_ticker


def test_unsubscribe_preserves_other_connections(manager):
    """Tests that unsubscribing one connection doesn't affect others."""
    ws1 = MagicMock()
    ws2 = MagicMock()
    manager.subscribe(ws1, "AAPL")
    manager.subscribe(ws2, "AAPL")
    manager.unsubscribe(ws1, "AAPL")

    assert manager.get_connection_count("AAPL") == 1


@pytest.mark.asyncio
async def test_broadcast_sends_to_all_subscribers(manager):
    """Tests that broadcast sends the message to all subscribers."""
    ws1 = AsyncMock()
    ws2 = AsyncMock()
    manager.subscribe(ws1, "AAPL")
    manager.subscribe(ws2, "AAPL")

    message = {"type": "deltas", "data": []}
    await manager.broadcast(message, "AAPL")

    ws1.send_json.assert_called_once_with(message)
    ws2.send_json.assert_called_once_with(message)


@pytest.mark.asyncio
async def test_broadcast_does_not_send_to_other_tickers(manager):
    """Tests that broadcast only sends to the target ticker."""
    ws_aapl = AsyncMock()
    ws_googl = AsyncMock()
    manager.subscribe(ws_aapl, "AAPL")
    manager.subscribe(ws_googl, "GOOGL")

    await manager.broadcast({"type": "deltas"}, "AAPL")

    ws_aapl.send_json.assert_called_once()
    ws_googl.send_json.assert_not_called()


@pytest.mark.asyncio
async def test_broadcast_removes_dead_connections(manager):
    """Tests that dead connections are cleaned up during broadcast."""
    ws_alive = AsyncMock()
    ws_dead = AsyncMock()
    ws_dead.send_json.side_effect = Exception("connection closed")
    manager.subscribe(ws_alive, "AAPL")
    manager.subscribe(ws_dead, "AAPL")

    await manager.broadcast({"type": "deltas"}, "AAPL")

    assert manager.get_connection_count("AAPL") == 1
    ws_alive.send_json.assert_called_once()


@pytest.mark.asyncio
async def test_broadcast_dead_connection_does_not_block_others(manager):
    """
    Tests that a dead connection doesn't prevent other clients from
    receiving the message.
    """
    ws_dead = AsyncMock()
    ws_dead.send_json.side_effect = Exception("connection closed")
    ws_alive = AsyncMock()
    manager.subscribe(ws_dead, "AAPL")
    manager.subscribe(ws_alive, "AAPL")

    message = {"type": "deltas", "data": []}
    await manager.broadcast(message, "AAPL")

    ws_alive.send_json.assert_called_once_with(message)


def test_get_connection_count_unknown_ticker(manager):
    """Tests that an unknown ticker returns 0 without side effects."""
    assert manager.get_connection_count("UNKNOWN") == 0
    # Should not create an entry in the defaultdict.
    assert "UNKNOWN" not in manager.connections_per_ticker
