import ipaddress
import logging
import socket
import struct
import time
from typing import Any
from urllib.parse import quote

import httpx
import orjson

from order_book_simulator.multicast.wire_format import DELTA, HEARTBEAT, decode

logger = logging.getLogger(__name__)


class MulticastSubscriber:
    """
    Receive per-symbol delta messages from a UDP multicast group and recover
    gaps deterministically via the HTTP recovery endpoints.
    """

    def __init__(
        self,
        group: str,
        port: int,
        recovery_base_url: str,
        burst_threshold: int = 10,
        http_timeout: float = 5.0,
        interface: str = "0.0.0.0",
    ):
        """Initialise the subscriber and join the multicast group.

        Args:
            group: IPv4 multicast group address (e.g. '239.1.1.1').
            port: UDP port to listen on.
            recovery_base_url: Base URL for order book recovery endpoints
                (e.g. 'http://localhost:8000/v1/order-book').
            burst_threshold: Missed-message count that switches recovery from
                incremental deltas to a full snapshot.
            http_timeout: Timeout in seconds for HTTP recovery requests.
            interface: Local IPv4 interface address used for IGMP membership.

        Raises:
            ValueError: If group is not an IPv4 multicast address or interface
                is not a valid IPv4 address.
        """
        if not ipaddress.IPv4Address(group).is_multicast:
            raise ValueError(f"group must be an IPv4 multicast address: {group!r}")
        ipaddress.IPv4Address(interface)

        self.group = group
        self.port = port
        self.interface = interface
        self.recovery_base_url = recovery_base_url.rstrip("/")
        self.burst_threshold = burst_threshold
        self.http_timeout = http_timeout
        self.expected_sequences: dict[str, int] = {}
        self.deltas_by_symbol: dict[str, list[dict[str, Any]]] = {}
        self.snapshots_by_symbol: dict[str, dict[str, Any]] = {}
        self._pending_deltas: dict[str, dict[int, dict[str, Any]]] = {}
        self.last_message_time: float = time.monotonic()

        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            # Bind to the multicast group rather than all interfaces so the
            # socket rejects unicast and unrelated multicast traffic on port.
            self.socket.bind((group, port))
            # Use two 4-byte IPv4 addresses for portable 8-byte struct ip_mreq.
            mreq = struct.pack(
                "4s4s", socket.inet_aton(group), socket.inet_aton(interface)
            )
            self.socket.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        except Exception:
            self.socket.close()
            raise

    @staticmethod
    def _parse_sequence_number(value: Any) -> int | None:
        """Parse a non-negative integer sequence number, rejecting booleans."""
        if isinstance(value, bool) or value is None:
            return None
        if isinstance(value, int):
            return value if value >= 0 else None
        if isinstance(value, str):
            try:
                parsed = int(value)
            except ValueError:
                return None
            return parsed if parsed >= 0 else None
        return None

    def _symbol_url(self, symbol: str) -> str:
        """Build the recovery URL for a specific symbol with path escaping."""
        return f"{self.recovery_base_url}/{quote(symbol, safe='')}"

    def _drain_pending(self, symbol: str) -> None:
        """Apply any buffered ahead-of-order live deltas now contiguous."""
        pending = self._pending_deltas.get(symbol)
        if not pending:
            return
        expected = self.expected_sequences.get(symbol, 1)
        # Drop any pending deltas already covered by snapshot or delta recovery.
        for seq in [s for s in pending if s < expected]:
            del pending[seq]
        while expected in pending:
            self.deltas_by_symbol.setdefault(symbol, []).append(pending.pop(expected))
            expected += 1
        self.expected_sequences[symbol] = expected
        if not pending:
            self._pending_deltas.pop(symbol, None)

    def _apply_snapshot(
        self, symbol: str, snapshot: dict[str, Any], snapshot_sequence: int
    ) -> bool:
        """Replace symbol state with a snapshot unless the snapshot is stale."""
        current_expected = self.expected_sequences.get(symbol, 1)
        if snapshot_sequence < current_expected - 1:
            return False
        self.snapshots_by_symbol[symbol] = snapshot
        self.deltas_by_symbol[symbol] = []
        self.expected_sequences[symbol] = snapshot_sequence + 1
        self._drain_pending(symbol)
        return True

    def _recover_snapshot(self, symbol: str) -> bool:
        """Fetch an atomic snapshot and sequence cursor for symbol."""
        try:
            response = httpx.get(self._symbol_url(symbol), timeout=self.http_timeout)
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning(f"Snapshot recovery failed for {symbol}: {exc}")
            return False
        if not isinstance(data, dict):
            logger.warning(f"Malformed snapshot recovery response for {symbol}")
            return False
        snapshot_seq = self._parse_sequence_number(
            data.get("current_delta_sequence_number")
        )
        book = data.get("book")
        if snapshot_seq is None or not isinstance(book, dict):
            logger.warning(f"Malformed snapshot recovery response for {symbol}")
            return False
        return self._apply_snapshot(symbol, book, snapshot_seq)

    def _recover_gap(
        self, symbol: str, from_sequence_number: int, target_exclusive: int
    ) -> None:
        """
        Recover missed messages in [from_sequence_number, target_exclusive - 1].

        Requests missed deltas from the HTTP endpoint when the gap is below
        burst_threshold, verifies exact contiguity, and falls back to snapshot
        recovery on burst loss, buffer eviction, or incomplete delta ranges.

        Args:
            symbol: Ticker symbol to recover.
            from_sequence_number: First missed sequence number (inclusive).
            target_exclusive: Sequence number one past the last missed delta.
        """
        gap_size = target_exclusive - from_sequence_number
        if gap_size <= 0:
            return

        if gap_size >= self.burst_threshold:
            self._recover_snapshot(symbol)
            return

        url = f"{self._symbol_url(symbol)}/deltas"
        try:
            response = httpx.get(
                url,
                params={"sequence_number": from_sequence_number - 1},
                timeout=self.http_timeout,
            )
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning(f"Delta gap recovery failed for {symbol}: {exc}")
            return
        if not isinstance(data, dict):
            logger.warning(f"Malformed delta recovery response for {symbol}")
            return

        snapshot = data.get("snapshot")
        if snapshot is not None:
            snapshot_seq = self._parse_sequence_number(
                data.get("current_delta_sequence_number")
            )
            if snapshot_seq is None or not isinstance(snapshot, dict):
                logger.warning(f"Malformed snapshot fallback response for {symbol}")
                return
            self._apply_snapshot(symbol, snapshot, snapshot_seq)
            return

        raw_deltas = data.get("deltas")
        if not isinstance(raw_deltas, list):
            logger.warning(f"Malformed deltas list in recovery response for {symbol}")
            return

        missing: list[tuple[int, dict[str, Any]]] = []
        for delta in raw_deltas:
            if not isinstance(delta, dict):
                logger.warning(
                    f"Malformed delta item in recovery response for {symbol}"
                )
                return
            delta_seq = self._parse_sequence_number(delta.get("sequence_number"))
            if delta_seq is None:
                logger.warning(
                    f"Malformed delta sequence_number in recovery response for {symbol}"
                )
                return
            if from_sequence_number <= delta_seq < target_exclusive:
                missing.append((delta_seq, delta))

        missing.sort(key=lambda item: item[0])
        expected_range = list(range(from_sequence_number, target_exclusive))
        if [seq for seq, _ in missing] != expected_range:
            # Delta range was incomplete or gapped - resync from snapshot.
            self._recover_snapshot(symbol)
            return

        self.deltas_by_symbol.setdefault(symbol, []).extend(
            delta for _, delta in missing
        )
        self.expected_sequences[symbol] = max(
            self.expected_sequences.get(symbol, 1), target_exclusive
        )
        self._drain_pending(symbol)

    def process_message(self, data: bytes) -> None:
        """
        Process a raw message received from the multicast socket.

        Decodes and validates the per-symbol wire format, updates liveness,
        and either applies an in-order delta, recovers a gap without
        duplicating live packets, buffers ahead-of-recovery deltas if recovery
        lags, or ignores an already-covered sequence number.

        Args:
            data: Raw bytes received from the UDP socket.
        """
        message_type, symbol, sequence_number, payload = decode(data)

        if message_type == HEARTBEAT:
            self.last_message_time = time.monotonic()
            if symbol:
                expected = self.expected_sequences.get(symbol, 1)
                if sequence_number >= expected:
                    self._recover_gap(symbol, expected, sequence_number + 1)
            return

        if message_type != DELTA:
            return

        deserialised_payload = orjson.loads(payload)
        if not isinstance(deserialised_payload, dict):
            raise TypeError("Multicast DELTA payload must decode to a JSON object.")

        self.last_message_time = time.monotonic()
        expected = self.expected_sequences.get(symbol, 1)
        if sequence_number < expected:
            return

        if sequence_number > expected:
            self._recover_gap(symbol, expected, sequence_number)
            expected = self.expected_sequences.get(symbol, 1)

        if sequence_number == expected:
            self.deltas_by_symbol.setdefault(symbol, []).append(deserialised_payload)
            self.expected_sequences[symbol] = sequence_number + 1
            self._drain_pending(symbol)
        elif sequence_number > expected:
            # Recovery lagged behind the live packet (or temporarily failed):
            # buffer this live delta within a small bound so it is applied once
            # the missing prefix arrives rather than silently dropped.
            pending = self._pending_deltas.setdefault(symbol, {})
            if len(pending) < self.burst_threshold:
                pending[sequence_number] = deserialised_payload

    def is_stale(self, timeout_seconds: float, now: float | None = None) -> bool:
        """Return True if no packet or heartbeat arrived within timeout_seconds."""
        current = time.monotonic() if now is None else now
        return (current - self.last_message_time) > timeout_seconds

    def receive_once(self, timeout_seconds: float, buffer_size: int = 65_535) -> bool:
        """
        Receive and process one datagram within timeout_seconds.

        Args:
            timeout_seconds: Socket receive deadline in seconds.
            buffer_size: Maximum UDP read buffer size in bytes.

        Returns:
            True if a valid datagram was received and processed, False on
            timeout or malformed packet.
        """
        self.socket.settimeout(timeout_seconds)
        try:
            data, _ = self.socket.recvfrom(buffer_size)
        except TimeoutError, BlockingIOError:
            return False
        try:
            self.process_message(data)
        except (ValueError, TypeError, orjson.JSONDecodeError) as exc:
            logger.warning(f"Discarding malformed multicast packet: {exc}")
            return False
        return True

    def close(self) -> None:
        """Close the UDP socket."""
        self.socket.close()
