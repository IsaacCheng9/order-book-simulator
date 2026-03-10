import asyncio
import socket
import struct
from order_book_simulator.multicast.wire_format import encode, DELTA, HEARTBEAT


class MulticastPublisher:
    """
    Publishes delta messages to a UDP multicast group using the binary wire
    format.
    """

    def __init__(self, group: str, port: int, ttl: int = 1) -> None:
        """
        Initialises the publisher with a UDP socket configured for multicast.

        Args:
            group: The multicast group address (e.g. '239.1.1.1').
            port: The destination port.
            ttl: Time-to-live for multicast packets. 1 means local network
                only.
        """
        self.group = group
        self.port = port
        self.ttl = ttl
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.setsockopt(
            socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, struct.pack("b", ttl)
        )
        self._stop_event: asyncio.Event = asyncio.Event()
        self._heartbeat_task: asyncio.Task[None] | None = None

    async def _heartbeat_loop(self, interval_seconds: float) -> None:
        while not self._stop_event.is_set():
            # Send a heartbeat with sequence number 0 as heartbeats carry no
            # data, meaning they don't need real sequence numbers.
            self.send_heartbeat(0)
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=interval_seconds,
                )
                break
            except asyncio.TimeoutError:
                # Interval elapsed - send next heartbeat.
                continue

    def send(self, sequence_number: int, payload: bytes) -> None:
        """
        Sends a delta message to the multicast group.

        Args:
            sequence_number: The delta sequence number.
            payload: The serialised delta payload bytes.
        """
        message = encode(DELTA, sequence_number, payload)
        self.socket.sendto(message, (self.group, self.port))

    def send_heartbeat(self, sequence_number: int) -> None:
        """
        Sends a heartbeat message with an empty payload.

        Allows subscribers to detect a dead stream if heartbeats stop arriving.

        Args:
            sequence_number: The current sequence number.
        """
        message = encode(HEARTBEAT, sequence_number, b"")
        self.socket.sendto(message, (self.group, self.port))

    async def start_heartbeat_task(self, interval_seconds: float = 1.0) -> None:
        self._heartbeat_task = asyncio.create_task(
            self._heartbeat_loop(interval_seconds)
        )

    async def stop_heartbeat_task(self) -> None:
        self._stop_event.set()
        if self._heartbeat_task:
            await self._heartbeat_task
            self._heartbeat_task = None

    async def close(self) -> None:
        """Closes the UDP socket."""
        await self.stop_heartbeat_task()
        self.socket.close()
