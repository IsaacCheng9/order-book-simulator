import asyncio
import logging
import math
import socket
import struct

from order_book_simulator.multicast.wire_format import DELTA, HEARTBEAT, encode

logger = logging.getLogger(__name__)


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
        self._latest_sequences: dict[str, int] = {}

    async def _heartbeat_loop(self, interval_seconds: float) -> None:
        """
        Send periodic heartbeats carrying each active symbol's latest sequence.

        Uses asyncio.wait_for on the stop event so stop_heartbeat_task() wakes
        the loop immediately instead of waiting for the sleep interval.

        Args:
            interval_seconds: Seconds between heartbeats.
        """
        while not self._stop_event.is_set():
            try:
                if self._latest_sequences:
                    for symbol, seq in list(self._latest_sequences.items()):
                        self.send_heartbeat(symbol, seq)
                else:
                    self.send_heartbeat("", 0)
            except OSError as exc:
                logger.warning(f"Multicast heartbeat send failed: {exc}")
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=interval_seconds,
                )
                break
            except TimeoutError:
                # Interval elapsed - send next heartbeat.
                continue

    def send(self, symbol: str, sequence_number: int, payload: bytes) -> None:
        """
        Send a per-symbol delta message to the multicast group.

        Args:
            symbol: Ticker symbol identifying the stream.
            sequence_number: Per-symbol delta sequence number.
            payload: Serialised delta payload bytes.
        """
        message = encode(DELTA, sequence_number, payload, symbol=symbol)
        self.socket.sendto(message, (self.group, self.port))
        self._latest_sequences[symbol] = max(
            sequence_number, self._latest_sequences.get(symbol, 0)
        )

    def send_heartbeat(self, symbol: str, sequence_number: int) -> None:
        """
        Send a heartbeat message carrying the current sequence for a symbol.

        Args:
            symbol: Ticker symbol, or empty string when idle with no symbols.
            sequence_number: Latest published sequence number for the symbol.
        """
        message = encode(HEARTBEAT, sequence_number, b"", symbol=symbol)
        self.socket.sendto(message, (self.group, self.port))

    async def start_heartbeat_task(self, interval_seconds: float = 1.0) -> None:
        """
        Start (or restart) the background heartbeat loop.

        Args:
            interval_seconds: Seconds between heartbeats (must be positive).

        Raises:
            ValueError: If interval_seconds is not positive.
        """
        if not math.isfinite(interval_seconds) or interval_seconds <= 0:
            raise ValueError("interval_seconds must be a finite positive number.")
        while self._heartbeat_task is not None and not self._heartbeat_task.done():
            if not self._stop_event.is_set():
                return
            await self._heartbeat_task
        self._stop_event.clear()
        self._heartbeat_task = asyncio.create_task(
            self._heartbeat_loop(interval_seconds)
        )

    async def stop_heartbeat_task(self) -> None:
        """
        Stops the background heartbeat loop and waits for it to finish.
        """
        self._stop_event.set()
        if self._heartbeat_task:
            await self._heartbeat_task
            self._heartbeat_task = None

    async def close(self) -> None:
        """Stops the heartbeat loop and closes the UDP socket."""
        await self.stop_heartbeat_task()
        self.socket.close()
