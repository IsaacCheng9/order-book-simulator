from unittest.mock import MagicMock

import httpx
import orjson
import pytest

from order_book_simulator.multicast import multicast_subscriber as sub_module
from order_book_simulator.multicast.multicast_subscriber import MulticastSubscriber
from order_book_simulator.multicast.wire_format import (
    DELTA,
    HEARTBEAT,
    encode,
)

GROUP = "239.1.1.1"
PORT = 5555
RECOVERY_BASE_URL = "http://localhost:8000/v1/order-book"


@pytest.fixture
def subscriber(monkeypatch):
    """Create a subscriber backed by a mocked UDP socket."""
    mock_sock = MagicMock()
    monkeypatch.setattr(sub_module.socket, "socket", lambda *a, **kw: mock_sock)
    sub = MulticastSubscriber(GROUP, PORT, RECOVERY_BASE_URL)
    yield sub
    sub.close()


def test_interleaved_two_symbol_feed_loses_neither_symbol(subscriber):
    """Track independent cursors per symbol so AAPL and MSFT never collide."""
    feed = [
        ("AAPL", 1),
        ("MSFT", 1),
        ("AAPL", 2),
        ("MSFT", 2),
    ]
    for symbol, seq in feed:
        payload = orjson.dumps({"symbol": symbol, "sequence_number": seq})
        subscriber.process_message(encode(DELTA, seq, payload, symbol=symbol))

    assert [d["sequence_number"] for d in subscriber.deltas_by_symbol["AAPL"]] == [1, 2]
    assert [d["sequence_number"] for d in subscriber.deltas_by_symbol["MSFT"]] == [1, 2]
    assert subscriber.expected_sequences == {"AAPL": 3, "MSFT": 3}


def test_gap_recovery_deduplicates_live_and_newer_deltas(subscriber, monkeypatch):
    """Recover [expected, live - 1] exactly when HTTP returns live + newer."""
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "deltas": [
            {"sequence_number": 2},
            {"sequence_number": 3},
            {"sequence_number": 4},
            {"sequence_number": 5},
        ],
        "current_delta_sequence_number": 5,
    }
    mock_get = MagicMock(return_value=mock_response)
    monkeypatch.setattr(httpx, "get", mock_get)

    subscriber.process_message(
        encode(DELTA, 1, orjson.dumps({"sequence_number": 1}), symbol="AAPL")
    )
    subscriber.process_message(
        encode(DELTA, 4, orjson.dumps({"sequence_number": 4}), symbol="AAPL")
    )

    mock_get.assert_called_once_with(
        f"{RECOVERY_BASE_URL}/AAPL/deltas",
        params={"sequence_number": 1},
        timeout=subscriber.http_timeout,
    )
    assert [d["sequence_number"] for d in subscriber.deltas_by_symbol["AAPL"]] == [
        1,
        2,
        3,
        4,
    ]
    assert subscriber.expected_sequences["AAPL"] == 5


def test_snapshot_recovery_discards_already_covered_live_packet(
    subscriber, monkeypatch
):
    """Discard triggering live delta when recovered snapshot is already ahead."""
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "snapshot": {"bids": [{"price": "100.00"}], "asks": []},
        "current_delta_sequence_number": 8,
    }
    monkeypatch.setattr(httpx, "get", lambda *a, **kw: mock_response)

    subscriber.process_message(
        encode(DELTA, 1, orjson.dumps({"sequence_number": 1}), symbol="AAPL")
    )
    # Triggering packet is seq 5, but snapshot is already at seq 8.
    subscriber.process_message(
        encode(DELTA, 5, orjson.dumps({"sequence_number": 5}), symbol="AAPL")
    )

    assert subscriber.snapshots_by_symbol["AAPL"] == {
        "bids": [{"price": "100.00"}],
        "asks": [],
    }
    assert subscriber.deltas_by_symbol["AAPL"] == []
    # Cursor advances to 9 and never moves backwards to 6.
    assert subscriber.expected_sequences["AAPL"] == 9


def test_burst_loss_uses_snapshot_endpoint_sequence(subscriber, monkeypatch):
    """Use current_delta_sequence_number from GET /{symbol} on burst loss."""
    subscriber.burst_threshold = 5
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "book": {"bids": [{"price": "100.00"}], "asks": []},
        "current_delta_sequence_number": 7,
    }
    mock_get = MagicMock(return_value=mock_response)
    monkeypatch.setattr(httpx, "get", mock_get)

    subscriber.process_message(
        encode(DELTA, 1, orjson.dumps({"sequence_number": 1}), symbol="AAPL")
    )
    subscriber.process_message(
        encode(DELTA, 7, orjson.dumps({"sequence_number": 7}), symbol="AAPL")
    )

    mock_get.assert_called_once_with(
        f"{RECOVERY_BASE_URL}/AAPL", timeout=subscriber.http_timeout
    )
    assert subscriber.deltas_by_symbol["AAPL"] == []
    assert subscriber.expected_sequences["AAPL"] == 8


def test_heartbeat_recovers_missing_tail_deltas_and_tracks_liveness(
    subscriber, monkeypatch
):
    """Recover missed tail deltas when a heartbeat reports a higher sequence."""
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "deltas": [{"sequence_number": 2}, {"sequence_number": 3}],
        "current_delta_sequence_number": 3,
    }
    monkeypatch.setattr(httpx, "get", lambda *a, **kw: mock_response)

    subscriber.process_message(
        encode(DELTA, 1, orjson.dumps({"sequence_number": 1}), symbol="AAPL")
    )
    subscriber.process_message(encode(HEARTBEAT, 3, b"", symbol="AAPL"))

    assert [d["sequence_number"] for d in subscriber.deltas_by_symbol["AAPL"]] == [
        1,
        2,
        3,
    ]
    assert subscriber.expected_sequences["AAPL"] == 4
    assert subscriber.is_stale(1.0, now=subscriber.last_message_time + 0.5) is False
    assert subscriber.is_stale(1.0, now=subscriber.last_message_time + 1.5) is True


def test_receive_once_returns_false_on_timeout(subscriber):
    """Expose receive deadline and report False when socket times out."""
    subscriber.socket.recvfrom.side_effect = TimeoutError()

    assert subscriber.receive_once(timeout_seconds=0.1) is False
    subscriber.socket.settimeout.assert_called_once_with(0.1)


def test_receive_once_handles_blocking_io_and_corrupt_packet(subscriber):
    """Return False on non-blocking BlockingIOError and malformed datagrams."""
    subscriber.socket.recvfrom.side_effect = BlockingIOError()
    assert subscriber.receive_once(timeout_seconds=0.0) is False

    subscriber.socket.recvfrom.side_effect = None
    subscriber.socket.recvfrom.return_value = (b"corrupt", ("127.0.0.1", 5555))
    assert subscriber.receive_once(timeout_seconds=0.1) is False


def test_stale_snapshot_is_ignored_without_wiping_deltas(subscriber, monkeypatch):
    """Ignore a stale recovery snapshot instead of wiping newer local state."""
    for seq in (1, 2, 3):
        subscriber.process_message(
            encode(DELTA, seq, orjson.dumps({"sequence_number": seq}), symbol="AAPL")
        )

    assert subscriber._apply_snapshot("AAPL", {"bids": [], "asks": []}, 1) is False
    assert [d["sequence_number"] for d in subscriber.deltas_by_symbol["AAPL"]] == [
        1,
        2,
        3,
    ]
    assert subscriber.expected_sequences["AAPL"] == 4


def test_lagging_snapshot_buffers_live_delta_until_gap_fills(subscriber, monkeypatch):
    """Buffer an ahead live delta if recovery snapshot lags behind it."""
    lagging_snapshot = MagicMock()
    lagging_snapshot.json.return_value = {
        "snapshot": {"bids": [{"price": "100.00"}], "asks": []},
        "current_delta_sequence_number": 3,
    }
    catchup_deltas = MagicMock()
    catchup_deltas.json.return_value = {
        "deltas": [{"sequence_number": 4}],
        "current_delta_sequence_number": 4,
    }
    responses = [lagging_snapshot, catchup_deltas]
    monkeypatch.setattr(httpx, "get", lambda *a, **kw: responses.pop(0))

    # Live seq 5 arrives while server snapshot only reached seq 3 (expected -> 4).
    subscriber.process_message(
        encode(DELTA, 5, orjson.dumps({"sequence_number": 5}), symbol="AAPL")
    )
    assert subscriber.expected_sequences["AAPL"] == 4
    assert subscriber.deltas_by_symbol["AAPL"] == []

    # Next heartbeat at seq 4 recovers seq 4 and drains buffered seq 5.
    subscriber.process_message(encode(HEARTBEAT, 4, b"", symbol="AAPL"))
    assert [d["sequence_number"] for d in subscriber.deltas_by_symbol["AAPL"]] == [4, 5]
    assert subscriber.expected_sequences["AAPL"] == 6


def test_recovery_handles_http_error_and_quotes_symbol_url(subscriber, monkeypatch):
    """Escape symbols in recovery URLs and survive HTTP status errors cleanly."""
    error_response = MagicMock()
    error_response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "500", request=MagicMock(), response=MagicMock()
    )
    called_urls: list[str] = []

    def fake_get(url, **kwargs):
        called_urls.append(url)
        return error_response

    monkeypatch.setattr(httpx, "get", fake_get)

    subscriber.process_message(
        encode(DELTA, 2, orjson.dumps({"sequence_number": 2}), symbol="BRK/B")
    )
    assert called_urls == [f"{RECOVERY_BASE_URL}/BRK%2FB/deltas"]
    assert subscriber.expected_sequences.get("BRK/B", 1) == 1


@pytest.mark.parametrize(
    "bad_body",
    [
        None,
        [],
        {"deltas": None},
        {"deltas": [{"missing_seq": 1}]},
        {"deltas": [{"sequence_number": True}]},
        {"snapshot": {"bids": []}, "current_delta_sequence_number": "not-an-int"},
    ],
)
def test_malformed_recovery_json_does_not_crash_or_drop_live_delta(
    subscriber, monkeypatch, bad_body
):
    """Ignore malformed recovery JSON and preserve the triggering live delta."""
    bad_response = MagicMock()
    bad_response.json.return_value = bad_body
    monkeypatch.setattr(httpx, "get", lambda *a, **kw: bad_response)

    subscriber.process_message(
        encode(DELTA, 2, orjson.dumps({"sequence_number": 2}), symbol="AAPL")
    )
    assert subscriber.expected_sequences.get("AAPL", 1) == 1
    assert 2 in subscriber._pending_deltas["AAPL"]
