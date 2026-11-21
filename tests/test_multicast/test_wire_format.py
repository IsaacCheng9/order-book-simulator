import pytest

from order_book_simulator.multicast.wire_format import (
    DELTA,
    HEADER_SIZE,
    HEARTBEAT,
    MAX_UDP_PAYLOAD_SIZE,
    decode,
    encode,
)


def test_round_trip_delta():
    """Verify encoding then decoding a delta returns original values."""
    payload = b'{"side": "BUY", "price": "100.00"}'
    data = encode(DELTA, 42, payload, symbol="AAPL")
    msg_type, symbol, seq, decoded_payload = decode(data)

    assert msg_type == DELTA
    assert symbol == "AAPL"
    assert seq == 42
    assert decoded_payload == payload


def test_round_trip_heartbeat():
    """Verify a heartbeat message round-trips with symbol and empty payload."""
    data = encode(HEARTBEAT, 99, b"", symbol="MSFT")
    msg_type, symbol, seq, payload = decode(data)

    assert msg_type == HEARTBEAT
    assert symbol == "MSFT"
    assert seq == 99
    assert payload == b""


def test_header_size():
    """Verify header plus symbol size matches encoded length."""
    data = encode(DELTA, 1, b"", symbol="AAPL")
    assert len(data) == HEADER_SIZE + len(b"AAPL")


def test_payload_preserved_exactly():
    """Verify binary payload bytes are preserved without mutation."""
    payload = bytes(range(256))
    data = encode(DELTA, 1, payload, symbol="AAPL")
    _, _, _, decoded_payload = decode(data)

    assert decoded_payload == payload


def test_encode_delta_requires_symbol():
    """Reject DELTA frames without a stream symbol."""
    with pytest.raises(ValueError, match="non-empty symbol"):
        encode(DELTA, 1, b"{}")


def test_encode_validates_sequence_range_and_heartbeat_invariants():
    """Reject out-of-range sequences, seq=0 deltas, and non-empty heartbeats."""
    with pytest.raises(ValueError, match="out of uint64 range"):
        encode(DELTA, -1, b"{}", symbol="AAPL")
    with pytest.raises(ValueError, match="out of uint64 range"):
        encode(DELTA, 2**64, b"{}", symbol="AAPL")
    with pytest.raises(ValueError, match="sequence_number >= 1"):
        encode(DELTA, 0, b"{}", symbol="AAPL")
    with pytest.raises(ValueError, match="empty payload"):
        encode(HEARTBEAT, 1, b"bad", symbol="AAPL")
    with pytest.raises(ValueError, match="sequence 0"):
        encode(HEARTBEAT, 1, b"", symbol="")

    # Idle heartbeat (symbol="", seq=0) round-trips cleanly.
    idle = encode(HEARTBEAT, 0, b"", symbol="")
    assert decode(idle) == (HEARTBEAT, "", 0, b"")


def test_encode_oversized_payload_raises():
    """Reject payloads exceeding the IPv4 UDP datagram limit."""
    oversized = b"\x00" * (MAX_UDP_PAYLOAD_SIZE - HEADER_SIZE)
    with pytest.raises(ValueError, match="Payload too long"):
        encode(DELTA, 1, oversized, symbol="AAPL")


def test_encode_max_payload_succeeds():
    """Accept a payload at the exact UDP datagram ceiling."""
    symbol = "AAPL"
    max_payload = b"\x00" * (MAX_UDP_PAYLOAD_SIZE - HEADER_SIZE - len(symbol))
    data = encode(DELTA, 1, max_payload, symbol=symbol)
    _, decoded_symbol, _, decoded_payload = decode(data)

    assert decoded_symbol == symbol
    assert len(decoded_payload) == len(max_payload)


def test_decode_rejects_short_truncated_trailing_and_unknown_frames():
    """Reject malformed datagrams strictly on decode."""
    with pytest.raises(ValueError, match="Data too short"):
        decode(b"\x00" * (HEADER_SIZE - 1))

    valid = encode(DELTA, 1, b"hello", symbol="AAPL")
    with pytest.raises(ValueError, match="Frame length mismatch"):
        decode(valid[:-1])
    with pytest.raises(ValueError, match="Frame length mismatch"):
        decode(valid + b"extra")

    bad_type = bytes([99]) + valid[1:]
    with pytest.raises(ValueError, match="Unknown message type"):
        decode(bad_type)


def test_sequence_number_large_value():
    """Verify uint64 sequence numbers round-trip cleanly."""
    large_seq = 2**64 - 1
    data = encode(DELTA, large_seq, b"test", symbol="AAPL")
    _, _, seq, _ = decode(data)

    assert seq == large_seq
