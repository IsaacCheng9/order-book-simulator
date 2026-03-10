import socket
import struct
from order_book_simulator.multicast.wire_format import encode, DELTA, HEARTBEAT


class MulticastPublisher:
    def __init__(self, group: str, port: int, ttl: int = 1) -> None:
        self.group = group
        self.port = port
        self.ttl = ttl
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.setsockopt(
            socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, struct.pack("b", ttl)
        )

    def send(self, sequence_number: int, payload: bytes) -> None:
        message = encode(DELTA, sequence_number, payload)
        self.socket.sendto(message, (self.group, self.port))

    def send_heartbeat(self, sequence_number: int) -> None:
        message = encode(HEARTBEAT, sequence_number, b"")
        self.socket.sendto(message, (self.group, self.port))

    def close(self) -> None:
        self.socket.close()
