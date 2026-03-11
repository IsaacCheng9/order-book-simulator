"""
Benchmarks comparing UDP multicast vs WebSocket fan-out.

The key advantage of multicast is fairness - one sendto() call delivers
to all subscribers simultaneously via the network layer, so publisher
cost is O(1) regardless of subscriber count. WebSocket fan-out is O(N)
because each client needs a separate enqueue.

Benchmarks:
1. Fan-out scaling - measures publisher-side cost per broadcast as
   subscriber count grows, comparing multicast (single sendto) vs
   WebSocket (N enqueues).
2. Per-message latency - measures end-to-end time from order book
   operation to publish for both paths at varying subscriber counts.
"""

import asyncio
import statistics
import time
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
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

NUM_RUNS = 5
NUM_BROADCASTS = 5_000
TICKER = "BENCH"


def _create_mock_publisher() -> MulticastPublisher:
    """Create a MulticastPublisher with a mocked UDP socket."""
    mock_socket_module = MagicMock()
    mock_socket_module.socket.return_value = MagicMock()
    original = pub_module.socket
    pub_module.socket = mock_socket_module  # type: ignore[assignment]
    publisher = MulticastPublisher(group="239.1.1.1", port=5555)
    pub_module.socket = original
    return publisher


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


async def benchmark_fan_out_scaling(
    subscriber_counts: list[int],
) -> None:
    """
    Compare publisher-side cost per broadcast as subscriber count
    grows.

    UDP Multicast: one sendto() per message regardless of subscribers.
    WebSocket: one enqueue() per subscriber per message.
    """
    print("Fan-Out Scaling: UDP Multicast vs WebSocket")
    print("-" * 70)
    print(
        f"{'Subscribers':>12} "
        f"{'UDP Multicast us/msg':>18} "
        f"{'WebSocket us/msg':>18} "
        f"{'Ratio':>8}"
    )

    payload = _create_delta_payload()
    ws_message = {
        "type": "deltas",
        "data": [orjson.loads(payload)],
    }

    for count in subscriber_counts:
        # UDP Multicast: single sendto per broadcast.
        mc_times: list[float] = []
        for _ in range(NUM_RUNS):
            publisher = _create_mock_publisher()
            start = time.perf_counter()
            for i in range(NUM_BROADCASTS):
                publisher.send(i + 1, payload)
            elapsed = time.perf_counter() - start
            mc_times.append((elapsed / NUM_BROADCASTS) * 1_000_000)

        # WebSocket: one enqueue per subscriber per broadcast.
        ws_times: list[float] = []
        for _ in range(NUM_RUNS):
            manager = WebSocketConnectionManager()
            websockets = [AsyncMock() for _ in range(count)]
            for ws in websockets:
                ws.send_json = AsyncMock()
                manager.subscribe(ws, TICKER)

            start = time.perf_counter()
            for _ in range(NUM_BROADCASTS):
                manager.broadcast(ws_message, TICKER)
            elapsed = time.perf_counter() - start
            ws_times.append((elapsed / NUM_BROADCASTS) * 1_000_000)
            await manager.close_all()

        mc_median = statistics.median(mc_times)
        ws_median = statistics.median(ws_times)
        ratio = ws_median / mc_median if mc_median > 0 else 0

        print(
            f"{count:>12,} {mc_median:>15.2f} us {ws_median:>15.2f} us {ratio:>7.2f}x"
        )


async def benchmark_publish_latency(
    subscriber_counts: list[int],
    num_operations: int = 500,
) -> None:
    """
    Measure end-to-end latency from order book operation to publish
    for both multicast and WebSocket at varying subscriber counts.

    UDP Multicast: add order + extract deltas + encode + sendto.
    WebSocket: add order + extract deltas + serialise + broadcast.
    """
    print("\nPublish Latency: Order Book -> Deliver (p50, in microseconds)")
    print("-" * 70)
    print(
        f"{'Subscribers':>12} {'UDP Multicast p50':>16} {'WebSocket p50':>16} {'Ratio':>8}"
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
                    "quantity": Decimal("100"),
                    "side": OrderSide.BUY,
                    "order_type": OrderType.LIMIT,
                    "created_at": datetime.now(timezone.utc),
                }
                seq_before = book.delta_buffer.current_sequence
                start = time.perf_counter()

                book.add_order(order)
                deltas = book.delta_buffer.get_deltas_since(seq_before)
                if deltas:
                    for d in deltas:
                        payload = orjson.dumps(asdict(d), default=str)
                        publisher.send(d.sequence_number, payload)

                elapsed = time.perf_counter() - start
                latencies.append(elapsed * 1_000_000)

            latencies.sort()
            mc_p50s.append(latencies[len(latencies) // 2])

        # WebSocket path.
        ws_p50s: list[float] = []
        for _ in range(NUM_RUNS):
            manager = WebSocketConnectionManager()
            websockets = [AsyncMock() for _ in range(count)]
            for ws in websockets:
                ws.send_json = AsyncMock()
                manager.subscribe(ws, TICKER)

            book = OrderBook(uuid4(), TICKER)
            latencies = []

            for idx in range(num_operations):
                order = {
                    "id": uuid4(),
                    "price": Decimal(100 + idx % 50),
                    "quantity": Decimal("100"),
                    "side": OrderSide.BUY,
                    "order_type": OrderType.LIMIT,
                    "created_at": datetime.now(timezone.utc),
                }
                seq_before = book.delta_buffer.current_sequence
                start = time.perf_counter()

                book.add_order(order)
                deltas = book.delta_buffer.get_deltas_since(seq_before)
                if deltas:
                    message = {
                        "type": "deltas",
                        "data": [asdict(d) for d in deltas],
                    }
                    manager.broadcast(message, TICKER)

                elapsed = time.perf_counter() - start
                latencies.append(elapsed * 1_000_000)

            latencies.sort()
            ws_p50s.append(latencies[len(latencies) // 2])
            await manager.close_all()

        mc_median = statistics.median(mc_p50s)
        ws_median = statistics.median(ws_p50s)
        ratio = ws_median / mc_median if mc_median > 0 else 0

        print(
            f"{count:>12,} {mc_median:>13.1f} us {ws_median:>13.1f} us {ratio:>7.2f}x"
        )


async def main() -> None:
    """Run all multicast benchmarks."""
    print("=" * 70)
    print("UDP Multicast vs WebSocket Benchmarks")
    print("=" * 70)
    print(f"Runs per benchmark: {NUM_RUNS} (reporting median)")
    print()

    # !IMPORTANT: Takes a long run time to complete for larger subscriber counts.
    subscriber_counts: list[int] = [
        1,
        10,
        50,
        100,
        500,
        1_000,
    ]

    await benchmark_fan_out_scaling(subscriber_counts)
    await benchmark_publish_latency(subscriber_counts)

    print()
    print("=" * 70)
    print("UDP Multicast benchmarks complete")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
