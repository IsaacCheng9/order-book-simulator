"""
Benchmark harness for WebSocket fan-out performance.

Measures broadcast throughput, push latency, and scaling behaviour of the
WebSocketConnectionManager under increasing subscriber counts. Uses mock
WebSocket connections so no Docker services are required.

Benchmarks:
- Fan-out throughput: messages enqueued per second across varying subscriber
  counts. Measures the cost of iterating N clients and calling put_nowait on
  each bounded queue.
- Push latency: end-to-end time from order book operation (add order, extract
  deltas, serialise) to broadcast completion, reported as p50/p95/p99
  percentiles.
- Push vs polling: compares the measured push latency against theoretical
  average polling latency at various intervals to quantify the server-push
  advantage.
- Snapshot vs delta bandwidth: compares total bytes sent if every update were a
  full snapshot vs the delta streaming approach.
"""

import asyncio
import statistics
import time
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import uuid4

import orjson

from order_book_simulator.common.models import OrderSide, OrderType
from order_book_simulator.gateway.ws_manager import WebSocketConnectionManager
from order_book_simulator.matching.order_book import OrderBook

NUM_RUNS = 5
TICKER = "BENCH"


def create_mock_websockets(count: int) -> list[AsyncMock]:
    """
    Creates mock WebSocket objects that track message receipt time.

    Args:
        count: The number of mock WebSocket connections to create.

    Returns:
        A list of mock WebSocket objects.
    """
    mocks = []
    for _ in range(count):
        ws = AsyncMock()
        ws.send_json = AsyncMock()
        mocks.append(ws)
    return mocks


def create_delta_message() -> dict:
    """Creates a realistic delta broadcast message."""
    return {
        "type": "deltas",
        "data": [
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
        ],
    }


async def benchmark_fan_out_throughput(
    subscriber_counts: list[int],
    num_broadcasts: int = 1_000,
) -> None:
    """
    Measures broadcast throughput for varying subscriber counts.

    For each subscriber count, broadcasts a delta message N times and
    measures the total messages delivered per second.

    Args:
        subscriber_counts: List of subscriber counts to test.
        num_broadcasts: Number of broadcasts per run.
    """
    print("Fan-Out Throughput Benchmark")
    print("-" * 60)
    print(
        f"{'Subscribers':>12} {'Broadcasts':>12} "
        f"{'Total msgs':>12} {'Median msg/s':>14} "
        f"{'Median ms/broadcast':>20}"
    )

    message = create_delta_message()

    for count in subscriber_counts:
        run_rates: list[float] = []
        run_latencies: list[float] = []

        for _ in range(NUM_RUNS):
            manager = WebSocketConnectionManager()
            websockets = create_mock_websockets(count)
            for ws in websockets:
                manager.subscribe(ws, TICKER)

            start = time.perf_counter()
            for _ in range(num_broadcasts):
                manager.broadcast(message, TICKER)
            elapsed = time.perf_counter() - start

            total_messages = count * num_broadcasts
            run_rates.append(total_messages / elapsed)
            run_latencies.append((elapsed / num_broadcasts) * 1_000)
            await manager.close_all()

        median_rate = statistics.median(run_rates)
        median_latency = statistics.median(run_latencies)
        print(
            f"{count:>12,} {num_broadcasts:>12,} "
            f"{count * num_broadcasts:>12,} "
            f"{median_rate:>14,.0f} {median_latency:>17.4f} ms"
        )


async def benchmark_push_latency(
    subscriber_counts: list[int],
    num_operations: int = 200,
) -> None:
    """
    Measures end-to-end push latency from order book operation to
    broadcast completion.

    Simulates the full path: order added to book, deltas extracted,
    serialised, and broadcast to all subscribers.

    Args:
        subscriber_counts: List of subscriber counts to test.
        num_operations: Number of order book operations per run.
    """
    print("\nPush Latency Benchmark (Order Book -> Broadcast)")
    print("-" * 60)
    print(
        f"{'Subscribers':>12} {'Operations':>12} "
        f"{'Median p50 us':>14} {'Median p95 us':>14} "
        f"{'Median p99 us':>14}"
    )

    for count in subscriber_counts:
        run_p50s: list[float] = []
        run_p95s: list[float] = []
        run_p99s: list[float] = []

        for _ in range(NUM_RUNS):
            manager = WebSocketConnectionManager()
            websockets = create_mock_websockets(count)
            for ws in websockets:
                manager.subscribe(ws, TICKER)

            book = OrderBook(uuid4(), TICKER)
            latencies: list[float] = []

            for i in range(num_operations):
                order = {
                    "id": uuid4(),
                    "price": Decimal(100 + i % 50),
                    "quantity": Decimal("100"),
                    "side": OrderSide.BUY,
                    "order_type": OrderType.LIMIT,
                    "created_at": datetime.now(timezone.utc),
                }

                # Measure the full path: add order, extract
                # deltas, serialise, and broadcast.
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
            p50_idx = len(latencies) // 2
            p95_idx = int(len(latencies) * 0.95)
            p99_idx = int(len(latencies) * 0.99)
            run_p50s.append(latencies[p50_idx])
            run_p95s.append(latencies[p95_idx])
            run_p99s.append(latencies[p99_idx])
            await manager.close_all()

        print(
            f"{count:>12,} {num_operations:>12,} "
            f"{statistics.median(run_p50s):>14,.1f} "
            f"{statistics.median(run_p95s):>14,.1f} "
            f"{statistics.median(run_p99s):>14,.1f}"
        )


async def benchmark_push_vs_polling(
    polling_intervals_ms: list[int],
    num_operations: int = 200,
) -> None:
    """
    Compares WebSocket push latency against polling intervals.

    Demonstrates the latency advantage of server-push over client
    polling at various intervals.

    Args:
        polling_intervals_ms: Polling intervals to compare against.
        num_operations: Number of operations to measure push
            latency over.
    """
    print("\nPush Latency vs Polling Comparison")
    print("-" * 60)

    # Measure actual push latency with 1 subscriber.
    run_medians: list[float] = []
    for _ in range(NUM_RUNS):
        manager = WebSocketConnectionManager()
        ws = AsyncMock()
        ws.send_json = AsyncMock()
        manager.subscribe(ws, TICKER)

        book = OrderBook(uuid4(), TICKER)
        latencies: list[float] = []

        for i in range(num_operations):
            order = {
                "id": uuid4(),
                "price": Decimal(100 + i % 50),
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
        run_medians.append(latencies[len(latencies) // 2])
        await manager.close_all()

    push_latency_us = statistics.median(run_medians)
    push_latency_ms = push_latency_us / 1_000

    print(f"WebSocket push latency (p50): {push_latency_us:,.1f} us")
    print()
    print(f"{'Polling interval':>18} {'Avg polling latency':>20} {'Speedup':>10}")
    for interval_ms in polling_intervals_ms:
        # Average polling latency is half the interval (uniform
        # distribution of event arrival within the interval).
        avg_polling_ms = interval_ms / 2
        speedup = avg_polling_ms / push_latency_ms
        print(f"{interval_ms:>15,} ms {avg_polling_ms:>17,.1f} ms {speedup:>9,.0f}x")


async def benchmark_snapshot_vs_delta_bandwidth(
    num_operations: int = 200,
) -> None:
    """
    Measures WebSocket bandwidth for full snapshots vs delta streaming.

    Builds a realistic order book and compares the total bytes sent
    if every update were a full snapshot vs the delta approach.

    Args:
        num_operations: Number of order book operations.
    """
    print("\nWebSocket Bandwidth: Snapshot vs Delta Streaming")
    print("-" * 60)

    book = OrderBook(uuid4(), TICKER)

    total_snapshot_bytes = 0
    total_delta_bytes = 0

    # Build up a book with 20 levels per side first.
    for i in range(100):
        book.add_order(
            {
                "id": uuid4(),
                "price": Decimal(100 + i % 20),
                "quantity": Decimal("100"),
                "side": OrderSide.BUY,
                "order_type": OrderType.LIMIT,
                "created_at": datetime.now(timezone.utc),
            }
        )
        book.add_order(
            {
                "id": uuid4(),
                "price": Decimal(200 + i % 20),
                "quantity": Decimal("100"),
                "side": OrderSide.SELL,
                "order_type": OrderType.LIMIT,
                "created_at": datetime.now(timezone.utc),
            }
        )

    # Now measure the streaming phase.
    for i in range(num_operations):
        seq_before = book.delta_buffer.current_sequence

        order = {
            "id": uuid4(),
            "price": Decimal(100 + i % 20),
            "quantity": Decimal("50"),
            "side": OrderSide.BUY if i % 3 != 0 else OrderSide.SELL,
            "order_type": OrderType.LIMIT,
            "created_at": datetime.now(timezone.utc),
        }
        book.add_order(order)

        # Snapshot approach: send full book state.
        snapshot_msg = {
            "type": "snapshot",
            "data": book.get_full_snapshot(),
        }
        total_snapshot_bytes += len(orjson.dumps(snapshot_msg, default=str))

        # Delta approach: send only the deltas.
        deltas = book.delta_buffer.get_deltas_since(seq_before)
        if deltas:
            delta_msg = {
                "type": "deltas",
                "data": [asdict(d) for d in deltas],
            }
            total_delta_bytes += len(orjson.dumps(delta_msg, default=str))

    reduction = (1 - total_delta_bytes / total_snapshot_bytes) * 100
    print(f"Operations:              {num_operations:,}")
    print(f"Total snapshot bytes:    {total_snapshot_bytes:,}")
    print(f"Total delta bytes:       {total_delta_bytes:,}")
    print(f"Bandwidth reduction:     {reduction:.1f}%")
    print(
        f"Avg snapshot per update: {total_snapshot_bytes / num_operations:,.0f} bytes"
    )
    print(f"Avg delta per update:   {total_delta_bytes / num_operations:,.0f} bytes")


async def main() -> None:
    """Runs all WebSocket benchmarks."""
    print("=" * 60)
    print("WebSocket Benchmarks")
    print("=" * 60)
    print(f"Runs per benchmark: {NUM_RUNS} (reporting median)")
    print()

    subscriber_counts = [1, 10, 50, 100, 500, 1_000]

    await benchmark_fan_out_throughput(subscriber_counts)
    await benchmark_push_latency(subscriber_counts)
    await benchmark_push_vs_polling([1, 10, 50, 100, 500, 1_000])
    await benchmark_snapshot_vs_delta_bandwidth()

    print()
    print("=" * 60)
    print("WebSocket benchmarks complete")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
