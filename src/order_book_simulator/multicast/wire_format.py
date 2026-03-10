import struct

# Network byte order:
# \! means network byte order (big-endian) - convention for wire protocols.
# B - unsigned byte (uint8, 1 byte) for message type
# Q - unsigned long long (uint64, 8 bytes) for sequence number
# H - unsigned short (uint16, 2 bytes) for payload length
HEADER_FORMAT = "!BQH"
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)  # 11 bytes
DELTA = 1
HEARTBEAT = 2


def encode(message_type: int, sequence_number: int, payload: bytes) -> bytes:
    """
    Encodes a message into the binary wire format.

    Packs an 11-byte fixed header (message type, sequence number, payload
    length) followed by the variable-length payload.

    Args:
        message_type: The message type (DELTA = 1, HEARTBEAT = 2).
        sequence_number: The monotonically increasing sequence
            number.
        payload: The payload bytes (e.g. orjson-serialised delta).

    Returns:
        The encoded binary message.

    Raises:
        ValueError: If the payload exceeds 65,535 bytes.
    """
    if len(payload) > 2**16 - 1:
        raise ValueError(
            f"Payload too long - needs to be less than {2**16 - 1} (2^16 - 1) bytes."
            f"Got {len(payload)} bytes."
        )

    header = struct.pack(
        HEADER_FORMAT,
        message_type,
        sequence_number,
        len(payload),
    )
    return header + payload


def decode(data: bytes) -> tuple[int, int, bytes]:
    """
    Decodes a binary wire format message.

    Unpacks the 11-byte fixed header and extracts the payload.

    Args:
        data: The raw bytes received from the UDP socket.

    Returns:
        A tuple of (message_type, sequence_number, payload).

    Raises:
        ValueError: If the data is shorter than the header size.
    """
    if len(data) < HEADER_SIZE:
        raise ValueError(f"Data too short - needs to be at least {HEADER_SIZE} bytes.")

    message_type, sequence_number, payload_length = struct.unpack(
        HEADER_FORMAT, data[:HEADER_SIZE]
    )
    return (
        message_type,
        sequence_number,
        data[HEADER_SIZE : HEADER_SIZE + payload_length],
    )
