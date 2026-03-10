import socket


class MulticastPublisher:
    def __init__(self, group: str, port: int, ttl: int = 1) -> None:
        self.group = group
        self.port = port
        self.ttl = ttl
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, ttl)

    def send(self, sequence_number: int, payload: bytes) -> None:
        pass

    def send_heartbeat(self, sequence_number: int) -> None:
        pass

    def close(self) -> None:
        self.socket.close()
