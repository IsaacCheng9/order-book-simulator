import struct

# Network byte order (big-endian):
# B - uint8 message type (DELTA=1, HEARTBEAT=2)
# Q - uint64 per-symbol sequence number
# H - uint16 payload length
# B - uint8 UTF-8 symbol length
HEADER_FORMAT = "!BQHB"
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)  # 12 bytes
MAX_SYMBOL_LENGTH = 255
MAX_SEQUENCE_NUMBER = 2**64 - 1
# Maximum IPv4 UDP datagram payload (65,535 - 20 IP header - 8 UDP header).
MAX_UDP_PAYLOAD_SIZE = 65_507
DELTA = 1
HEARTBEAT = 2
VALID_MESSAGE_TYPES = frozenset({DELTA, HEARTBEAT})


def encode(
    message_type: int,
    sequence_number: int,
    payload: bytes,
    symbol: str = "",
) -> bytes:
    """
    Encode a message into the binary per-symbol wire format.

    Packs a 12-byte fixed header followed by UTF-8 symbol bytes and the
    variable-length payload.

    Args:
        message_type: Message type (DELTA = 1, HEARTBEAT = 2).
        sequence_number: Monotonically increasing per-symbol sequence number.
        payload: Serialised payload bytes.
        symbol: Ticker symbol identifying the stream (required for DELTA).

    Returns:
        The encoded binary datagram.

    Raises:
        ValueError: If message_type, sequence_number, symbol, or payload sizes
            are invalid.
    """
    if message_type not in VALID_MESSAGE_TYPES:
        raise ValueError(f"Unknown message type: {message_type}")

    if sequence_number < 0 or sequence_number > MAX_SEQUENCE_NUMBER:
        raise ValueError(f"Sequence number out of uint64 range: {sequence_number}")

    symbol_bytes = symbol.encode("utf-8")
    if message_type == DELTA:
        if not symbol_bytes:
            raise ValueError("DELTA messages require a non-empty symbol.")
        if sequence_number < 1:
            raise ValueError("DELTA messages require sequence_number >= 1.")
    elif message_type == HEARTBEAT:
        if payload:
            raise ValueError("HEARTBEAT messages must have an empty payload.")
        if not symbol_bytes and sequence_number != 0:
            raise ValueError(
                "Idle HEARTBEAT messages without a symbol must use sequence 0."
            )

    if len(symbol_bytes) > MAX_SYMBOL_LENGTH:
        raise ValueError(
            f"Symbol too long - max {MAX_SYMBOL_LENGTH} bytes, got {len(symbol_bytes)}."
        )

    max_payload = MAX_UDP_PAYLOAD_SIZE - HEADER_SIZE - len(symbol_bytes)
    if len(payload) > max_payload:
        raise ValueError(
            f"Payload too long - needs to be at most {max_payload} bytes. "
            f"Got {len(payload)} bytes."
        )

    header = struct.pack(
        HEADER_FORMAT,
        message_type,
        sequence_number,
        len(payload),
        len(symbol_bytes),
    )
    return header + symbol_bytes + payload


def decode(data: bytes) -> tuple[int, str, int, bytes]:
    """
    Decode a binary wire-format message and validate framing strictly.

    Args:
        data: Raw bytes received from the UDP socket.

    Returns:
        A tuple of (message_type, symbol, sequence_number, payload).

    Raises:
        ValueError: If the frame is truncated, has trailing bytes, has an
            unknown message type, or violates message invariants.
    """
    if len(data) < HEADER_SIZE:
        raise ValueError(f"Data too short - needs to be at least {HEADER_SIZE} bytes.")

    message_type, sequence_number, payload_length, symbol_length = struct.unpack(
        HEADER_FORMAT, data[:HEADER_SIZE]
    )
    if message_type not in VALID_MESSAGE_TYPES:
        raise ValueError(f"Unknown message type: {message_type}")

    expected_length = HEADER_SIZE + symbol_length + payload_length
    if len(data) != expected_length:
        raise ValueError(
            f"Frame length mismatch - expected {expected_length} bytes, "
            f"got {len(data)} bytes."
        )

    symbol_end = HEADER_SIZE + symbol_length
    try:
        symbol = data[HEADER_SIZE:symbol_end].decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("Invalid UTF-8 symbol in multicast frame.") from exc

    payload = data[symbol_end:expected_length]
    if message_type == DELTA:
        if not symbol:
            raise ValueError("DELTA messages require a non-empty symbol.")
        if sequence_number < 1:
            raise ValueError("DELTA messages require sequence_number >= 1.")
    elif message_type == HEARTBEAT:
        if payload:
            raise ValueError("HEARTBEAT messages must have an empty payload.")
        if not symbol and sequence_number != 0:
            raise ValueError(
                "Idle HEARTBEAT messages without a symbol must use sequence 0."
            )

    return (
        message_type,
        symbol,
        sequence_number,
        payload,
    )
