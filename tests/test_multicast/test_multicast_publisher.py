import asyncio
from unittest.mock import MagicMock

import pytest

from order_book_simulator.multicast import multicast_publisher as pub_module
from order_book_simulator.multicast.multicast_publisher import MulticastPublisher
from order_book_simulator.multicast.wire_format import (
    DELTA,
    HEARTBEAT,
    decode,
)

GROUP = "239.1.1.1"
PORT = 5555


@pytest.fixture
def mock_socket(monkeypatch):
    """Patches the socket constructor to return a MagicMock."""
    mock_sock = MagicMock()
    monkeypatch.setattr(pub_module.socket, "socket", lambda *a, **kw: mock_sock)
    return mock_sock


def test_send_delta(mock_socket):
    """Tests that send encodes a delta and sends to the multicast group."""
    publisher = MulticastPublisher(GROUP, PORT)
    payload = b'{"side": "BUY"}'
    publisher.send(1, payload)

    mock_socket.sendto.assert_called_once()
    sent_data, dest = mock_socket.sendto.call_args[0]
    assert dest == (GROUP, PORT)

    msg_type, seq, decoded_payload = decode(sent_data)
    assert msg_type == DELTA
    assert seq == 1
    assert decoded_payload == payload


def test_send_heartbeat(mock_socket):
    """Tests that send_heartbeat sends a heartbeat with empty payload."""
    publisher = MulticastPublisher(GROUP, PORT)
    publisher.send_heartbeat(42)

    mock_socket.sendto.assert_called_once()
    sent_data, dest = mock_socket.sendto.call_args[0]
    assert dest == (GROUP, PORT)

    msg_type, seq, payload = decode(sent_data)
    assert msg_type == HEARTBEAT
    assert seq == 42
    assert payload == b""


@pytest.mark.asyncio
async def test_close(mock_socket):
    """Tests that close shuts down the socket."""
    publisher = MulticastPublisher(GROUP, PORT)
    await publisher.close()

    mock_socket.close.assert_called_once()


def test_socket_configured_with_multicast_ttl(mock_socket):
    """Tests that the socket is configured with the correct TTL."""
    MulticastPublisher(GROUP, PORT, ttl=3)

    mock_socket.setsockopt.assert_called_once()


def test_multiple_sends_use_same_socket(mock_socket):
    """Tests that multiple sends reuse the same socket."""
    publisher = MulticastPublisher(GROUP, PORT)
    publisher.send(1, b"first")
    publisher.send(2, b"second")
    publisher.send_heartbeat(3)

    assert mock_socket.sendto.call_count == 3


@pytest.mark.asyncio
async def test_heartbeat_loop_sends_periodically(mock_socket):
    """Tests that the heartbeat loop sends multiple heartbeats."""
    publisher = MulticastPublisher(GROUP, PORT)
    await publisher.start_heartbeat_task(interval_seconds=0.05)
    # Let a few heartbeats fire.
    await asyncio.sleep(0.18)
    await publisher.stop_heartbeat_task()

    # Should have sent at least 3 heartbeats.
    assert mock_socket.sendto.call_count >= 3
    # Every message should be a heartbeat.
    for call in mock_socket.sendto.call_args_list:
        sent_data = call[0][0]
        msg_type, _, payload = decode(sent_data)
        assert msg_type == HEARTBEAT
        assert payload == b""


@pytest.mark.asyncio
async def test_stop_heartbeat_exits_immediately(mock_socket):
    """Tests that stop_heartbeat_task wakes the loop without waiting."""
    publisher = MulticastPublisher(GROUP, PORT)
    await publisher.start_heartbeat_task(interval_seconds=10.0)
    # Stop immediately - should not block for 10 seconds.
    await publisher.stop_heartbeat_task()

    assert publisher._heartbeat_task is None


@pytest.mark.asyncio
async def test_close_stops_heartbeat_and_socket(mock_socket):
    """Tests that close stops the heartbeat and closes the socket."""
    publisher = MulticastPublisher(GROUP, PORT)
    await publisher.start_heartbeat_task(interval_seconds=0.05)
    await asyncio.sleep(0.08)
    await publisher.close()

    assert publisher._heartbeat_task is None
    mock_socket.close.assert_called_once()


@pytest.mark.asyncio
async def test_stop_heartbeat_without_start_is_noop(mock_socket):
    """Tests that stopping without starting does not raise."""
    publisher = MulticastPublisher(GROUP, PORT)
    await publisher.stop_heartbeat_task()

    assert publisher._heartbeat_task is None
