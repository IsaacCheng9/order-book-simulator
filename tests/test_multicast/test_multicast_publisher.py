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
    """Patch the socket module on the publisher to return a MagicMock."""
    mock_sock = MagicMock()
    mock_socket_module = MagicMock()
    mock_socket_module.socket.return_value = mock_sock
    monkeypatch.setattr(pub_module, "socket", mock_socket_module)
    return mock_sock


def test_send_delta(mock_socket):
    """Encode a per-symbol delta and send it to the multicast group."""
    publisher = MulticastPublisher(GROUP, PORT)
    payload = b'{"side": "BUY"}'
    publisher.send("AAPL", 1, payload)

    mock_socket.sendto.assert_called_once()
    sent_data, dest = mock_socket.sendto.call_args[0]
    assert dest == (GROUP, PORT)

    msg_type, symbol, seq, decoded_payload = decode(sent_data)
    assert msg_type == DELTA
    assert symbol == "AAPL"
    assert seq == 1
    assert decoded_payload == payload


def test_send_heartbeat(mock_socket):
    """Send a per-symbol heartbeat with an empty payload."""
    publisher = MulticastPublisher(GROUP, PORT)
    publisher.send_heartbeat("AAPL", 42)

    mock_socket.sendto.assert_called_once()
    sent_data, dest = mock_socket.sendto.call_args[0]
    assert dest == (GROUP, PORT)

    msg_type, symbol, seq, payload = decode(sent_data)
    assert msg_type == HEARTBEAT
    assert symbol == "AAPL"
    assert seq == 42
    assert payload == b""


@pytest.mark.asyncio
async def test_close(mock_socket):
    """Close shuts down the socket."""
    publisher = MulticastPublisher(GROUP, PORT)
    await publisher.close()

    mock_socket.close.assert_called_once()


def test_socket_configured_with_multicast_ttl(mock_socket):
    """Configure the socket with the requested multicast TTL."""
    MulticastPublisher(GROUP, PORT, ttl=3)

    mock_socket.setsockopt.assert_called_once()


@pytest.mark.asyncio
async def test_heartbeat_loop_carries_real_per_symbol_sequences(mock_socket):
    """Emit heartbeats carrying each active symbol's latest sequence."""
    publisher = MulticastPublisher(GROUP, PORT)
    publisher.send("AAPL", 7, b"{}")
    publisher.send("MSFT", 3, b"{}")
    mock_socket.sendto.reset_mock()

    await publisher.start_heartbeat_task(interval_seconds=0.05)
    await asyncio.sleep(0.08)
    await publisher.stop_heartbeat_task()

    seen: dict[str, set[int]] = {}
    for call in mock_socket.sendto.call_args_list:
        msg_type, symbol, seq, payload = decode(call[0][0])
        assert msg_type == HEARTBEAT
        assert payload == b""
        seen.setdefault(symbol, set()).add(seq)

    assert seen["AAPL"] == {7}
    assert seen["MSFT"] == {3}


@pytest.mark.asyncio
async def test_heartbeat_task_can_restart_after_stop(mock_socket):
    """Clear the stop event so restarting the heartbeat loop sends again."""
    publisher = MulticastPublisher(GROUP, PORT)
    await publisher.start_heartbeat_task(interval_seconds=0.05)
    await asyncio.sleep(0.03)
    await publisher.stop_heartbeat_task()

    first_count = mock_socket.sendto.call_count
    assert first_count >= 1

    await publisher.start_heartbeat_task(interval_seconds=0.05)
    await asyncio.sleep(0.08)
    await publisher.stop_heartbeat_task()

    assert mock_socket.sendto.call_count > first_count


@pytest.mark.asyncio
async def test_stop_heartbeat_exits_immediately(mock_socket):
    """Wake the heartbeat loop immediately without waiting for the interval."""
    publisher = MulticastPublisher(GROUP, PORT)
    await publisher.start_heartbeat_task(interval_seconds=10.0)
    await publisher.stop_heartbeat_task()

    assert publisher._heartbeat_task is None


@pytest.mark.asyncio
async def test_stop_heartbeat_without_start_is_noop(mock_socket):
    """Stopping without starting does not raise."""
    publisher = MulticastPublisher(GROUP, PORT)
    await publisher.stop_heartbeat_task()

    assert publisher._heartbeat_task is None


def test_failed_send_does_not_pollute_latest_sequences(mock_socket):
    """Leave _latest_sequences unchanged when encode or sendto fails."""
    publisher = MulticastPublisher(GROUP, PORT)
    mock_socket.sendto.side_effect = OSError("network unreachable")

    with pytest.raises(OSError, match="network unreachable"):
        publisher.send("AAPL", 5, b"{}")

    assert "AAPL" not in publisher._latest_sequences


@pytest.mark.asyncio
async def test_heartbeat_loop_survives_transient_os_error(mock_socket):
    """Continue sending heartbeats after a transient socket OSError."""
    publisher = MulticastPublisher(GROUP, PORT)
    publisher.send("AAPL", 2, b"{}")
    mock_socket.sendto.reset_mock()

    calls = 0

    def flaky_sendto(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("temporary failure")

    mock_socket.sendto.side_effect = flaky_sendto

    await publisher.start_heartbeat_task(interval_seconds=0.03)
    await asyncio.sleep(0.1)
    await publisher.stop_heartbeat_task()

    assert calls >= 2


@pytest.mark.asyncio
async def test_start_heartbeat_rejects_non_positive_interval(mock_socket):
    """Reject non-positive heartbeat intervals before creating a task."""
    publisher = MulticastPublisher(GROUP, PORT)
    with pytest.raises(ValueError, match="positive"):
        await publisher.start_heartbeat_task(interval_seconds=0)
