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
    header = struct.pack(
        HEADER_FORMAT,
        message_type,
        sequence_number,
        len(payload),
    )
    return header + payload
