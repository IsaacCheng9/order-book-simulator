from unittest.mock import MagicMock

import httpx
import orjson
import pytest

from order_book_simulator.multicast import multicast_subscriber as sub_module
from order_book_simulator.multicast.wire_format import (
    DELTA,
    HEARTBEAT,
    encode,
)

GROUP = "239.1.1.1"
PORT = 5555
RECOVERY_URL = "http://localhost:8000/v1/order-book/TEST"


@pytest.fixture
def subscriber(monkeypatch):
    """Creates a subscriber with mocked socket."""
    monkeypatch.setattr(sub_module.socket, "socket", lambda *a, **kw: MagicMock())
    from order_book_simulator.multicast.multicast_subscriber import (
        MulticastSubscriber,
    )

    sub = MulticastSubscriber(GROUP, PORT, RECOVERY_URL)
    yield sub
    sub.close()


# --- In-order processing ---
def test_process_in_order_delta(subscriber):
    """Tests that an in-order delta is appended to the deltas list."""
    payload = orjson.dumps({"side": "BUY", "price": "100.00"})
    data = encode(DELTA, 1, payload)
    subscriber.process_message(data)

    assert len(subscriber.deltas) == 1
    assert subscriber.deltas[0]["side"] == "BUY"
    assert subscriber.expected_sequence == 2


def test_process_multiple_in_order_deltas(subscriber):
    """Tests that consecutive in-order deltas are all processed."""
    for i in range(1, 4):
        payload = orjson.dumps({"seq": i})
        subscriber.process_message(encode(DELTA, i, payload))

    assert len(subscriber.deltas) == 3
    assert subscriber.expected_sequence == 4


def test_process_heartbeat_advances_sequence(subscriber):
    """Tests that a heartbeat advances the expected sequence."""
    subscriber.process_message(encode(HEARTBEAT, 1, b""))

    assert len(subscriber.deltas) == 0
    assert subscriber.expected_sequence == 2


# --- Duplicate / old messages ---
def test_process_duplicate_ignored(subscriber):
    """Tests that a duplicate sequence number is ignored."""
    payload = orjson.dumps({"seq": 1})
    subscriber.process_message(encode(DELTA, 1, payload))
    # Send the same sequence again.
    subscriber.process_message(encode(DELTA, 1, payload))

    assert len(subscriber.deltas) == 1
    assert subscriber.expected_sequence == 2


def test_process_old_message_ignored(subscriber):
    """Tests that an old sequence number is ignored."""
    subscriber.expected_sequence = 5
    payload = orjson.dumps({"seq": 3})
    subscriber.process_message(encode(DELTA, 3, payload))

    assert len(subscriber.deltas) == 0
    assert subscriber.expected_sequence == 5


# --- Gap detection and delta recovery ---
def test_gap_triggers_delta_recovery(subscriber, monkeypatch):
    """Tests that a gap triggers HTTP delta recovery."""
    recovered_deltas = [{"seq": 2}, {"seq": 3}]
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "deltas": recovered_deltas,
        "current_delta_sequence_number": 4,
    }
    monkeypatch.setattr(httpx, "get", lambda *a, **kw: mock_response)

    # Process seq 1 in order.
    subscriber.process_message(encode(DELTA, 1, orjson.dumps({"seq": 1})))

    # Skip seq 2 and 3, send seq 4.
    subscriber.process_message(encode(DELTA, 4, orjson.dumps({"seq": 4})))

    # Should have: seq 1 (in order) + seq 2, 3 (recovered) + seq 4.
    assert len(subscriber.deltas) == 4
    assert subscriber.expected_sequence == 5


def test_gap_recovery_calls_correct_url(subscriber, monkeypatch):
    """Tests that gap recovery calls the deltas endpoint."""
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "deltas": [],
        "current_delta_sequence_number": 3,
    }
    mock_get = MagicMock(return_value=mock_response)
    monkeypatch.setattr(httpx, "get", mock_get)

    subscriber.process_message(encode(DELTA, 3, orjson.dumps({"seq": 3})))

    mock_get.assert_called_once_with(
        f"{RECOVERY_URL}/deltas",
        params={"sequence_number": 0},
    )


# --- Snapshot fallback (evicted sequence) ---
def test_evicted_sequence_falls_back_to_snapshot(subscriber, monkeypatch):
    """Tests that an evicted sequence triggers snapshot fallback."""
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "snapshot": {"bids": [], "asks": []},
        "current_delta_sequence_number": 5,
    }
    monkeypatch.setattr(httpx, "get", lambda *a, **kw: mock_response)

    # Process seq 1 in order.
    subscriber.process_message(encode(DELTA, 1, orjson.dumps({"seq": 1})))
    assert len(subscriber.deltas) == 1

    # Gap with evicted response - deltas should be cleared.
    subscriber.process_message(encode(DELTA, 5, orjson.dumps({"seq": 5})))

    assert subscriber.snapshot == {"bids": [], "asks": []}
    # Deltas cleared, then seq 5 appended.
    assert len(subscriber.deltas) == 1
    assert subscriber.deltas[0]["seq"] == 5


# --- Burst loss (full snapshot recovery) ---
def test_burst_loss_triggers_snapshot_recovery(subscriber, monkeypatch):
    """Tests that a burst loss triggers full snapshot recovery."""
    subscriber.burst_threshold = 5

    mock_response = MagicMock()
    mock_response.json.return_value = {
        "book": {"bids": [{"price": "100.00"}], "asks": []},
    }
    mock_get = MagicMock(return_value=mock_response)
    monkeypatch.setattr(httpx, "get", mock_get)

    # Process seq 1 in order.
    subscriber.process_message(encode(DELTA, 1, orjson.dumps({"seq": 1})))

    # Jump to seq 7 (gap of 6 >= burst_threshold of 5).
    subscriber.process_message(encode(DELTA, 7, orjson.dumps({"seq": 7})))

    # Should have called the base URL, not /deltas.
    mock_get.assert_called_once_with(RECOVERY_URL)
    assert subscriber.snapshot == {
        "bids": [{"price": "100.00"}],
        "asks": [],
    }
    # Deltas cleared, then seq 7 appended.
    assert len(subscriber.deltas) == 1
    assert subscriber.expected_sequence == 8


# --- Heartbeat with gap ---
def test_heartbeat_with_gap_triggers_recovery(subscriber, monkeypatch):
    """Tests that a heartbeat after a gap triggers recovery."""
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "deltas": [{"seq": 2}],
        "current_delta_sequence_number": 3,
    }
    monkeypatch.setattr(httpx, "get", lambda *a, **kw: mock_response)

    # Process seq 1 in order.
    subscriber.process_message(encode(DELTA, 1, orjson.dumps({"seq": 1})))

    # Heartbeat at seq 3 - gap of 1 (missed seq 2).
    subscriber.process_message(encode(HEARTBEAT, 3, b""))

    # Recovered seq 2 via HTTP, heartbeat not appended.
    assert len(subscriber.deltas) == 2
    assert subscriber.expected_sequence == 4
