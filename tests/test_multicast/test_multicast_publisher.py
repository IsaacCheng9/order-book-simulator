import asyncio
from unittest.mock import MagicMock, patch

import pytest

from order_book_simulator.multicast.multicast_publisher import MulticastPublisher
from order_book_simulator.multicast.wire_format import (
    DELTA,
    HEARTBEAT,
    decode,
)

GROUP = "239.1.1.1"
PORT = 5555


@patch("order_book_simulator.multicast.multicast_publisher.socket.socket")
def test_send_delta(mock_socket_cls):
    """Tests that send encodes a delta and sends to the multicast group."""
    mock_sock = MagicMock()
    mock_socket_cls.return_value = mock_sock

    publisher = MulticastPublisher(GROUP, PORT)
    payload = b'{"side": "BUY"}'
    publisher.send(1, payload)

    mock_sock.sendto.assert_called_once()
    sent_data, dest = mock_sock.sendto.call_args[0]
    assert dest == (GROUP, PORT)

    msg_type, seq, decoded_payload = decode(sent_data)
    assert msg_type == DELTA
    assert seq == 1
    assert decoded_payload == payload


@patch("order_book_simulator.multicast.multicast_publisher.socket.socket")
def test_send_heartbeat(mock_socket_cls):
    """Tests that send_heartbeat sends a heartbeat with empty payload."""
    mock_sock = MagicMock()
    mock_socket_cls.return_value = mock_sock

    publisher = MulticastPublisher(GROUP, PORT)
    publisher.send_heartbeat(42)

    mock_sock.sendto.assert_called_once()
    sent_data, dest = mock_sock.sendto.call_args[0]
    assert dest == (GROUP, PORT)

    msg_type, seq, payload = decode(sent_data)
    assert msg_type == HEARTBEAT
    assert seq == 42
    assert payload == b""


@pytest.mark.asyncio
@patch("order_book_simulator.multicast.multicast_publisher.socket.socket")
async def test_close(mock_socket_cls):
    """Tests that close shuts down the socket."""
    mock_sock = MagicMock()
    mock_socket_cls.return_value = mock_sock

    publisher = MulticastPublisher(GROUP, PORT)
    await publisher.close()

    mock_sock.close.assert_called_once()


@patch("order_book_simulator.multicast.multicast_publisher.socket.socket")
def test_socket_configured_with_multicast_ttl(mock_socket_cls):
    """Tests that the socket is configured with the correct TTL."""
    mock_sock = MagicMock()
    mock_socket_cls.return_value = mock_sock

    MulticastPublisher(GROUP, PORT, ttl=3)

    mock_sock.setsockopt.assert_called_once()


@patch("order_book_simulator.multicast.multicast_publisher.socket.socket")
def test_multiple_sends_use_same_socket(mock_socket_cls):
    """Tests that multiple sends reuse the same socket."""
    mock_sock = MagicMock()
    mock_socket_cls.return_value = mock_sock

    publisher = MulticastPublisher(GROUP, PORT)
    publisher.send(1, b"first")
    publisher.send(2, b"second")
    publisher.send_heartbeat(3)

    assert mock_sock.sendto.call_count == 3


@pytest.mark.asyncio
@patch("order_book_simulator.multicast.multicast_publisher.socket.socket")
async def test_heartbeat_loop_sends_periodically(mock_socket_cls):
    """Tests that the heartbeat loop sends multiple heartbeats."""
    mock_sock = MagicMock()
    mock_socket_cls.return_value = mock_sock

    publisher = MulticastPublisher(GROUP, PORT)
    await publisher.start_heartbeat_task(interval_seconds=0.05)
    # Let a few heartbeats fire.
    await asyncio.sleep(0.18)
    await publisher.stop_heartbeat_task()

    # Should have sent at least 3 heartbeats.
    assert mock_sock.sendto.call_count >= 3
    # Every message should be a heartbeat.
    for call in mock_sock.sendto.call_args_list:
        sent_data = call[0][0]
        msg_type, _, payload = decode(sent_data)
        assert msg_type == HEARTBEAT
        assert payload == b""


@pytest.mark.asyncio
@patch("order_book_simulator.multicast.multicast_publisher.socket.socket")
async def test_stop_heartbeat_exits_immediately(mock_socket_cls):
    """Tests that stop_heartbeat_task wakes the loop without waiting."""
    mock_sock = MagicMock()
    mock_socket_cls.return_value = mock_sock

    publisher = MulticastPublisher(GROUP, PORT)
    await publisher.start_heartbeat_task(interval_seconds=10.0)
    # Stop immediately - should not block for 10 seconds.
    await publisher.stop_heartbeat_task()

    assert publisher._heartbeat_task is None


@pytest.mark.asyncio
@patch("order_book_simulator.multicast.multicast_publisher.socket.socket")
async def test_close_stops_heartbeat_and_socket(mock_socket_cls):
    """Tests that close stops the heartbeat and closes the socket."""
    mock_sock = MagicMock()
    mock_socket_cls.return_value = mock_sock

    publisher = MulticastPublisher(GROUP, PORT)
    await publisher.start_heartbeat_task(interval_seconds=0.05)
    await asyncio.sleep(0.08)
    await publisher.close()

    assert publisher._heartbeat_task is None
    mock_sock.close.assert_called_once()


@pytest.mark.asyncio
@patch("order_book_simulator.multicast.multicast_publisher.socket.socket")
async def test_stop_heartbeat_without_start_is_noop(mock_socket_cls):
    """Tests that stopping without starting does not raise."""
    mock_sock = MagicMock()
    mock_socket_cls.return_value = mock_sock

    publisher = MulticastPublisher(GROUP, PORT)
    await publisher.stop_heartbeat_task()

    assert publisher._heartbeat_task is None
