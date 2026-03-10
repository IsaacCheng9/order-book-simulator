import pytest

from order_book_simulator.multicast.wire_format import (
    DELTA,
    HEADER_SIZE,
    HEARTBEAT,
    decode,
    encode,
)


def test_round_trip_delta():
    """Tests that encoding then decoding a delta returns original values."""
    payload = b'{"side": "BUY", "price": "100.00"}'
    data = encode(DELTA, 42, payload)
    msg_type, seq, decoded_payload = decode(data)

    assert msg_type == DELTA
    assert seq == 42
    assert decoded_payload == payload


def test_round_trip_heartbeat():
    """Tests that a heartbeat message round-trips with empty payload."""
    data = encode(HEARTBEAT, 99, b"")
    msg_type, seq, payload = decode(data)

    assert msg_type == HEARTBEAT
    assert seq == 99
    assert payload == b""


def test_header_size():
    """Tests that the header is exactly 11 bytes."""
    data = encode(DELTA, 1, b"")
    assert len(data) == HEADER_SIZE


def test_payload_preserved_exactly():
    """Tests that binary payload bytes are preserved without mutation."""
    payload = bytes(range(256))
    data = encode(DELTA, 1, payload)
    _, _, decoded_payload = decode(data)

    assert decoded_payload == payload


def test_encode_oversized_payload_raises():
    """Tests that a payload exceeding uint16 max raises ValueError."""
    oversized = b"\x00" * (2**16)
    with pytest.raises(ValueError, match="Payload too long"):
        encode(DELTA, 1, oversized)


def test_encode_max_payload_succeeds():
    """Tests that a payload at exactly uint16 max is accepted."""
    max_payload = b"\x00" * (2**16 - 1)
    data = encode(DELTA, 1, max_payload)
    _, _, decoded_payload = decode(data)

    assert len(decoded_payload) == 2**16 - 1


def test_decode_too_short_raises():
    """Tests that data shorter than the header raises ValueError."""
    with pytest.raises(ValueError, match="Data too short"):
        decode(b"\x00" * 10)


def test_decode_empty_data_raises():
    """Tests that empty data raises ValueError."""
    with pytest.raises(ValueError, match="Data too short"):
        decode(b"")


def test_sequence_number_large_value():
    """Tests that large sequence numbers (uint64) are handled."""
    large_seq = 2**64 - 1
    data = encode(DELTA, large_seq, b"test")
    _, seq, _ = decode(data)

    assert seq == large_seq
