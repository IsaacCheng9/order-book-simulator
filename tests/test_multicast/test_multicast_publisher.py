from unittest.mock import MagicMock, patch

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


@patch("order_book_simulator.multicast.multicast_publisher.socket.socket")
def test_close(mock_socket_cls):
    """Tests that close shuts down the socket."""
    mock_sock = MagicMock()
    mock_socket_cls.return_value = mock_sock

    publisher = MulticastPublisher(GROUP, PORT)
    publisher.close()

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
