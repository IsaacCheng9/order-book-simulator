import orjson
from typing import Any
import httpx
import socket
import struct

from order_book_simulator.multicast.wire_format import DELTA, decode


class MulticastSubscriber:
    """
    Receives delta messages from a UDP multicast group and recovers gaps via
    the HTTP recovery endpoint.
    """

    def __init__(
        self, group: str, port: int, recovery_base_url: str, burst_threshold: int = 10
    ):
        """
        Initialises the subscriber and joins the multicast group.

        Args:
            group: The multicast group address (e.g. '239.1.1.1').
            port: The port to listen on.
            recovery_base_url: The base URL for the HTTP recovery endpoint
                (e.g. 'http://localhost:8000/v1/order-book/AAPL').
            burst_threshold: The number of consecutive missed messages that
                triggers a full snapshot recovery instead of individual delta
                recovery.
        """
        self.group = group
        self.port = port
        self.recovery_base_url = recovery_base_url
        self.burst_threshold = burst_threshold
        self.expected_sequence = 1
        self.deltas: list[dict[str, Any]] = []
        self.snapshot: dict[str, Any] | None = None

        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.socket.bind(("", port))
        mreq = struct.pack("4sL", socket.inet_aton(group), socket.INADDR_ANY)
        self.socket.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)

    def _recover_gap(self, from_sequence_number: int, to_sequence_number: int) -> None:
        """
        Recovers missed messages between two sequence numbers.

        Requests missed deltas from the HTTP endpoint if the gap is
        small, or falls back to a full snapshot if the gap exceeds
        the burst threshold or the requested range has been evicted.

        Args:
            from_sequence_number: The first missed sequence number.
            to_sequence_number: The sequence number that was actually received.
        """
        gap_size = to_sequence_number - from_sequence_number

        # Too many deltas missed, so we need to request a full snapshot from
        # the recovery server.
        if gap_size >= self.burst_threshold:
            response = httpx.get(self.recovery_base_url)
            data: dict[str, Any] = response.json()
            self.snapshot = data["book"]
            self.deltas.clear()
        # Recover the gap by requesting the missing deltas from the recovery
        # server.
        else:
            url = f"{self.recovery_base_url}/deltas"
            response = httpx.get(
                url, params={"sequence_number": from_sequence_number - 1}
            )
            data = response.json()
            if "deltas" in data:
                self.deltas.extend(data["deltas"])
            # Snapshot fallback.
            else:
                self.snapshot = data["snapshot"]
                self.deltas.clear()

    def process_message(self, data: bytes) -> None:
        """
        Processes a raw message received from the multicast socket.

        Decodes the wire format, then either processes the message in order,
        recovers a gap, or ignores a duplicate.

        Args:
            data: The raw bytes received from the UDP socket.
        """
        message_type, sequence_number, payload = decode(data)

        # We're up-to-date, so process the message.
        if sequence_number == self.expected_sequence:
            if message_type == DELTA:
                deserialised_payload: dict[str, Any] = orjson.loads(payload)
                self.deltas.append(deserialised_payload)
            self.expected_sequence += 1
        # We've missed some deltas, so recover the gap before processing the
        # current message.
        elif sequence_number > self.expected_sequence:
            self._recover_gap(self.expected_sequence, sequence_number)
            if message_type == DELTA:
                deserialised_payload: dict[str, Any] = orjson.loads(payload)
                self.deltas.append(deserialised_payload)
            self.expected_sequence = sequence_number + 1
        # Already seen this sequence number, so ignore it silently.
        else:
            pass

    def close(self) -> None:
        """Closes the UDP socket."""
        self.socket.close()
