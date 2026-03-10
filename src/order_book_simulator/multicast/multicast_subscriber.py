import socket
import struct


class MulticastSubscriber:
    def __init__(
        self, group: str, port: int, recovery_base_url: str, burst_threshold: int = 10
    ):
        self.group = group
        self.port = port
        self.recovery_base_url = recovery_base_url
        self.burst_threshold = burst_threshold
        self.expected_sequence = 1

        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.socket.bind(("", port))
        mreq = struct.pack("4sL", socket.inet_aton(group), socket.INADDR_ANY)
        self.socket.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)

    def _recover_gap(self, from_sequence_number: int, to_sequence_number: int) -> None:
        pass

    def process_message(self, data: bytes) -> None:
        pass

    def close(self) -> None:
        pass
