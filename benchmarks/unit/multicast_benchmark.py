"""Benchmarks comparing UDP multicast vs WebSocket fan-out.

Measures both:
1. In-process CPU/scheduling scaling (mocked socket/WebSocket I/O) with active
   WebSocket draining so all subscribers stay connected and receive 100% of
   broadcasts (no silent slow-consumer disconnects).
2. Real one-way UDP loopback multicast delivery (127.0.0.1) from
   MulticastPublisher through the OS UDP stack into
   MulticastSubscriber.receive_once() after an unmeasured warmup phase.
"""

import asyncio
import socket
import statistics
import time
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import orjson

from order_book_simulator.common.models import OrderSide, OrderType
from order_book_simulator.gateway.ws_manager import (
    WebSocketConnectionManager,
)
from order_book_simulator.matching.order_book import OrderBook
from order_book_simulator.multicast import (
    multicast_publisher as pub_module,
)
from order_book_simulator.multicast.multicast_publisher import (
    MulticastPublisher,
)
from order_book_simulator.multicast.multicast_subscriber import (
    MulticastSubscriber,
)

NUM_RUNS = 5
NUM_BROADCASTS = 500
BATCH_DRAIN_SIZE = 50
TICKER = "BENCH"
LOOPBACK_GROUP = "239.1.1.99"
UDP_WARMUP_OPS = 50
UDP_MEASURED_OPS = 500


def _create_mock_publisher() -> MulticastPublisher:
    """Create a MulticastPublisher with a mocked UDP socket."""
    mock_socket_module = MagicMock()
    mock_socket_module.socket.return_value = MagicMock()
    with patch.object(pub_module, "socket", mock_socket_module):
        return MulticastPublisher(group="239.1.1.1", port=5555)


def _create_delta_payload() -> bytes:
    """Create a realistic serialised delta payload."""
    return orjson.dumps(
        {
            "sequence_number": 1,
            "timestamp": time.time(),
            "delta_type": "LEVEL_UPDATE",
            "ticker": TICKER,
            "side": "BUY",
            "price": "100.00",
            "quantity": "500.00",
            "order_count": 5,
        }
    )


def _create_draining_websockets(count: int) -> tuple[list[AsyncMock], list[int]]:
    """Create mock WebSockets tracking total delivered messages."""
    delivered = [0]
    mocks: list[AsyncMock] = []
    for _ in range(count):
        ws = AsyncMock()

        async def _send_json(_msg: Any) -> None:
            delivered[0] += 1

        ws.send_json = AsyncMock(side_effect=_send_json)
        mocks.append(ws)
    return mocks, delivered


async def _wait_for_delivery(
    delivered: list[int],
    expected: int,
    *,
    manager: WebSocketConnectionManager,
    ticker: str,
    expected_subscribers: int,
    timeout_seconds: float = 10.0,
) -> None:
    """Wait until all subscribers deliver expected msgs or fail fast on drop."""
    deadline = time.monotonic() + timeout_seconds
    while delivered[0] < expected:
        active = len(manager.connections_per_ticker.get(ticker, {}))
        if active < expected_subscribers:
            raise AssertionError(
                f"Subscriber disconnected early: active={active}, "
                f"expected={expected_subscribers}, delivered={delivered[0]}/{expected}"
            )
        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"Timed out waiting for async delivery: "
                f"delivered={delivered[0]}/{expected}"
            )
        await asyncio.sleep(0)


async def benchmark_fan_out_scaling(
    subscriber_counts: list[int],
) -> None:
    """Compare publisher-side cost per broadcast as subscriber count grows.

    UDP Multicast: one encode + mocked sendto() per broadcast.
    WebSocket: N enqueues + async sender drain per broadcast (100% delivered).
    """
    print("Fan-Out Scaling (Mocked I/O, 100% Delivered): UDP Multicast vs WebSocket")
    print("-" * 76)
    print(
        f"{'Subscribers':>12} "
        f"{'UDP Multicast us/msg':>20} "
        f"{'WebSocket us/msg':>20} "
        f"{'Ratio':>10}"
    )

    payload = _create_delta_payload()
    ws_message = {
        "type": "deltas",
        "data": [orjson.loads(payload)],
    }

    for count in subscriber_counts:
        # UDP Multicast: single encode + sendto per broadcast.
        mc_times: list[float] = []
        for _ in range(NUM_RUNS):
            publisher = _create_mock_publisher()
            start = time.perf_counter()
            for i in range(NUM_BROADCASTS):
                publisher.send(TICKER, i + 1, payload)
            elapsed = time.perf_counter() - start
            mc_times.append((elapsed / NUM_BROADCASTS) * 1_000_000)

        # WebSocket: broadcast + active drain so zero clients disconnect.
        ws_times: list[float] = []
        for _ in range(NUM_RUNS):
            manager = WebSocketConnectionManager()
            websockets, delivered = _create_draining_websockets(count)
            for ws in websockets:
                manager.subscribe(ws, TICKER)

            total_expected = count * NUM_BROADCASTS
            start = time.perf_counter()
            for idx in range(1, NUM_BROADCASTS + 1):
                manager.broadcast(ws_message, TICKER)
                if idx % BATCH_DRAIN_SIZE == 0:
                    await _wait_for_delivery(
                        delivered,
                        idx * count,
                        manager=manager,
                        ticker=TICKER,
                        expected_subscribers=count,
                    )
            await _wait_for_delivery(
                delivered,
                total_expected,
                manager=manager,
                ticker=TICKER,
                expected_subscribers=count,
            )
            elapsed = time.perf_counter() - start

            assert len(manager.connections_per_ticker.get(TICKER, {})) == count
            assert delivered[0] == total_expected
            ws_times.append((elapsed / NUM_BROADCASTS) * 1_000_000)
            await manager.close_all()

        mc_median = statistics.median(mc_times)
        ws_median = statistics.median(ws_times)
        ratio = ws_median / mc_median if mc_median > 0 else 0

        print(
            f"{count:>12,} {mc_median:>17.2f} us {ws_median:>17.2f} us {ratio:>9.2f}x"
        )


async def benchmark_publish_latency(
    subscriber_counts: list[int],
    num_operations: int = 200,
) -> None:
    """Measure order book -> publish/drain latency (p50, mocked I/O)."""
    print("\nPublish Latency (Mocked I/O, Order Book -> Deliver, p50 us)")
    print("-" * 76)
    print(
        f"{'Subscribers':>12} {'UDP Multicast p50':>18} "
        f"{'WebSocket p50':>18} {'Ratio':>10}"
    )

    for count in subscriber_counts:
        # UDP Multicast path.
        mc_p50s: list[float] = []
        for _ in range(NUM_RUNS):
            publisher = _create_mock_publisher()
            book = OrderBook(uuid4(), TICKER)
            latencies: list[float] = []

            for idx in range(num_operations):
                order = {
                    "id": uuid4(),
                    "price": Decimal(100 + idx % 50),
                    "quantity": Decimal(100),
                    "side": OrderSide.BUY,
                    "order_type": OrderType.LIMIT,
                    "created_at": datetime.now(UTC),
                }
                start = time.perf_counter()

                book.add_order(order)
                for d in book.last_deltas:
                    payload = orjson.dumps(asdict(d), default=str)
                    publisher.send(d.ticker, d.sequence_number, payload)

                elapsed = time.perf_counter() - start
                latencies.append(elapsed * 1_000_000)

            latencies.sort()
            mc_p50s.append(latencies[len(latencies) // 2])

        # WebSocket path (drained per operation so every client receives it).
        ws_p50s: list[float] = []
        for _ in range(NUM_RUNS):
            manager = WebSocketConnectionManager()
            websockets, delivered = _create_draining_websockets(count)
            for ws in websockets:
                manager.subscribe(ws, TICKER)

            book = OrderBook(uuid4(), TICKER)
            latencies = []

            for idx in range(num_operations):
                order = {
                    "id": uuid4(),
                    "price": Decimal(100 + idx % 50),
                    "quantity": Decimal(100),
                    "side": OrderSide.BUY,
                    "order_type": OrderType.LIMIT,
                    "created_at": datetime.now(UTC),
                }
                expected_before = delivered[0]
                start = time.perf_counter()

                book.add_order(order)
                deltas = book.last_deltas
                assert len(deltas) == 1
                message = {
                    "type": "deltas",
                    "data": [asdict(deltas[0])],
                }
                manager.broadcast(message, TICKER)
                await _wait_for_delivery(
                    delivered,
                    expected_before + count,
                    manager=manager,
                    ticker=TICKER,
                    expected_subscribers=count,
                )

                elapsed = time.perf_counter() - start
                latencies.append(elapsed * 1_000_000)

            assert len(manager.connections_per_ticker.get(TICKER, {})) == count
            latencies.sort()
            ws_p50s.append(latencies[len(latencies) // 2])
            await manager.close_all()

        mc_median = statistics.median(mc_p50s)
        ws_median = statistics.median(ws_p50s)
        ratio = ws_median / mc_median if mc_median > 0 else 0

        print(
            f"{count:>12,} {mc_median:>15.1f} us {ws_median:>15.1f} us {ratio:>9.2f}x"
        )


def _find_free_multicast_port(group: str) -> int:
    """Pick an available UDP port by probing the multicast group address."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((group, 0))
        except OSError:
            sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


async def benchmark_loopback_udp_multicast(
    warmup_operations: int = UDP_WARMUP_OPS,
    num_operations: int = UDP_MEASURED_OPS,
) -> None:
    """Measure one-way loopback UDP multicast delivery (publisher -> subscriber)."""
    print("\nReal One-Way Loopback UDP Multicast Delivery (127.0.0.1 Publish -> Recv)")
    print("-" * 76)

    subscriber: MulticastSubscriber | None = None
    publisher: MulticastPublisher | None = None
    try:
        try:
            port = _find_free_multicast_port(LOOPBACK_GROUP)
            subscriber = MulticastSubscriber(
                group=LOOPBACK_GROUP,
                port=port,
                recovery_base_url="http://127.0.0.1:8000/v1/order-book",
                interface="127.0.0.1",
            )
            publisher = MulticastPublisher(group=LOOPBACK_GROUP, port=port, ttl=1)
            publisher.socket.setsockopt(
                socket.IPPROTO_IP,
                socket.IP_MULTICAST_IF,
                socket.inet_aton("127.0.0.1"),
            )
            publisher.socket.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
        except OSError as exc:
            print(f"Skipped real loopback UDP multicast (host unsupported): {exc}")
            return

        # Pre-flight probe: skip only if the host does not route loopback
        # multicast at all; fail explicitly on any mid-run packet loss later.
        probe_payload = _create_delta_payload()
        publisher.send("PROBE", 1, probe_payload)
        if not subscriber.receive_once(timeout_seconds=1.0):
            print(
                "Skipped real loopback UDP multicast "
                "(loopback multicast packets not routed on this host)."
            )
            return

        book = OrderBook(uuid4(), TICKER)
        latencies: list[float] = []
        total_ops = warmup_operations + num_operations
        sent_deltas = 0

        for idx in range(total_ops):
            order = {
                "id": uuid4(),
                "price": Decimal(100 + idx % 50),
                "quantity": Decimal(100),
                "side": OrderSide.BUY,
                "order_type": OrderType.LIMIT,
                "created_at": datetime.now(UTC),
            }
            start = time.perf_counter()
            book.add_order(order)
            deltas = book.last_deltas
            assert len(deltas) == 1
            delta = deltas[0]
            payload = orjson.dumps(asdict(delta), default=str)
            publisher.send(delta.ticker, delta.sequence_number, payload)
            sent_deltas += 1
            if not subscriber.receive_once(timeout_seconds=1.0):
                raise RuntimeError(
                    f"Mid-run loopback UDP packet loss at operation {idx + 1}/"
                    f"{total_ops} (seq={delta.sequence_number})"
                )
            elapsed = time.perf_counter() - start
            if idx >= warmup_operations:
                latencies.append(elapsed * 1_000_000)

        latencies.sort()
        p50 = latencies[len(latencies) // 2]
        p95 = latencies[int(len(latencies) * 0.95)]
        p99 = latencies[int(len(latencies) * 0.99)]
        assert subscriber.expected_sequences[TICKER] == sent_deltas + 1
        print(
            f"Delivered {num_operations:,} one-way datagrams (after "
            f"{warmup_operations} warmup) over 127.0.0.1 "
            f"(p50: {p50:,.1f} us, p95: {p95:,.1f} us, p99: {p99:,.1f} us)"
        )
    finally:
        if subscriber is not None:
            subscriber.close()
        if publisher is not None:
            await publisher.close()


async def main() -> None:
    """Run all multicast benchmarks."""
    print("=" * 76)
    print("UDP Multicast vs WebSocket Benchmarks")
    print("=" * 76)
    print(f"Runs per in-process benchmark: {NUM_RUNS} (reporting median)")
    print()

    subscriber_counts: list[int] = [1, 10, 50, 100, 500, 1_000]
    await benchmark_fan_out_scaling(subscriber_counts)
    await benchmark_publish_latency(subscriber_counts)
    await benchmark_loopback_udp_multicast()

    print()
    print("=" * 76)
    print("UDP Multicast benchmarks complete")
    print("=" * 76)


if __name__ == "__main__":
    asyncio.run(main())
