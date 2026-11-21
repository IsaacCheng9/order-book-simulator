"""Benchmark harness for WebSocket fan-out and loopback delivery performance.

Measures two distinct layers so results never conflate in-process async
scheduling cost with real network I/O:

1. In-process async delivery (mock WebSocket): measures broadcast plus asyncio
   sender-task drain across varying subscriber counts, verifying 100% message
   delivery with zero slow-consumer disconnects and reporting both broadcast
   rate (broadcasts/s) and total subscriber deliveries (broadcasts x N / s).
2. Real loopback WebSocket delivery (127.0.0.1 TCP): runs a minimal ASGI
   WebSocket server over loopback with real aiohttp client(s), measuring
   end-to-end all-subscriber fan-out completion latency from OrderBook.add_order
   through FastAPI/Uvicorn and TCP loopback to client JSON receipt, after an
   unmeasured warmup phase.
3. Bandwidth: compares full snapshot vs delta JSON payload sizes.
"""

import asyncio
import contextlib
import socket
import statistics
import time
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import aiohttp
import orjson
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from order_book_simulator.common.models import Delta, OrderSide, OrderType
from order_book_simulator.gateway.ws_manager import WebSocketConnectionManager
from order_book_simulator.matching.order_book import OrderBook

NUM_RUNS = 5
TICKER = "BENCH"
LOOPBACK_WARMUP_OPS = 50
LOOPBACK_MEASURED_OPS = 500


class _NoSignalUvicornServer(uvicorn.Server):
    """Uvicorn server subclass that leaves the host process signal handlers intact."""

    def install_signal_handlers(self) -> None:
        return None


def _delta_to_json_dict(delta: Delta) -> dict[str, Any]:
    """Serialise a Delta into a JSON-ready dict without double JSON encoding."""
    return {
        "sequence_number": delta.sequence_number,
        "timestamp": delta.timestamp,
        "delta_type": delta.delta_type.value,
        "ticker": delta.ticker,
        "side": delta.side.value if delta.side is not None else None,
        "price": str(delta.price),
        "quantity": str(delta.quantity),
        "order_count": delta.order_count,
        "trade_id": str(delta.trade_id) if delta.trade_id is not None else None,
    }


def create_mock_websockets(
    count: int,
) -> tuple[list[AsyncMock], list[int]]:
    """Create mock WebSockets that count delivered messages when drained."""
    delivered = [0]
    mocks: list[AsyncMock] = []
    for _ in range(count):
        ws = AsyncMock()

        async def _send_json(_msg: Any) -> None:
            delivered[0] += 1

        ws.send_json = AsyncMock(side_effect=_send_json)
        mocks.append(ws)
    return mocks, delivered


def create_delta_message(seq: int = 1) -> dict[str, Any]:
    """Create a realistic delta broadcast message."""
    return {
        "type": "deltas",
        "data": [
            {
                "sequence_number": seq,
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


async def benchmark_fan_out_throughput(
    subscriber_counts: list[int],
    num_broadcasts: int = 500,
    batch_drain_size: int = 50,
) -> None:
    """Measure async fan-out delivery throughput with active draining.

    Drains every batch_drain_size broadcasts (below the default queue size of 64)
    so subscribers stay connected and every message is genuinely delivered.
    """
    print("In-Process Async Fan-Out Delivery (Mock WebSocket, 100% Delivered)")
    print("-" * 82)
    print(
        f"{'Subscribers':>12} {'Broadcasts/s':>14} "
        f"{'Delivered msg/s':>18} {'Median ms/broadcast':>20}"
    )

    message = create_delta_message()

    for count in subscriber_counts:
        run_broadcast_rates: list[float] = []
        run_delivery_rates: list[float] = []
        run_latencies: list[float] = []

        for _ in range(NUM_RUNS):
            manager = WebSocketConnectionManager()
            websockets, delivered = create_mock_websockets(count)
            for ws in websockets:
                manager.subscribe(ws, TICKER)

            total_expected = count * num_broadcasts
            start = time.perf_counter()
            for idx in range(1, num_broadcasts + 1):
                manager.broadcast(message, TICKER)
                if idx % batch_drain_size == 0:
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
            run_broadcast_rates.append(num_broadcasts / elapsed)
            run_delivery_rates.append(total_expected / elapsed)
            run_latencies.append((elapsed / num_broadcasts) * 1_000)
            await manager.close_all()

        print(
            f"{count:>12,} "
            f"{statistics.median(run_broadcast_rates):>14,.0f} "
            f"{statistics.median(run_delivery_rates):>18,.0f} "
            f"{statistics.median(run_latencies):>17.4f} ms"
        )


async def benchmark_push_latency(
    subscriber_counts: list[int],
    num_operations: int = 200,
) -> None:
    """Measure per-operation order book -> async sender delivery latency."""
    print("\nIn-Process Push Latency (Order Book -> Mock send_json Complete)")
    print("-" * 82)
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
            websockets, delivered = create_mock_websockets(count)
            for ws in websockets:
                manager.subscribe(ws, TICKER)

            book = OrderBook(uuid4(), TICKER)
            latencies: list[float] = []

            for i in range(num_operations):
                order = {
                    "id": uuid4(),
                    "price": Decimal(100 + i % 50),
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
                    "data": [_delta_to_json_dict(deltas[0])],
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
            run_p50s.append(latencies[len(latencies) // 2])
            run_p95s.append(latencies[int(len(latencies) * 0.95)])
            run_p99s.append(latencies[int(len(latencies) * 0.99)])
            await manager.close_all()

        print(
            f"{count:>12,} {num_operations:>12,} "
            f"{statistics.median(run_p50s):>14,.1f} "
            f"{statistics.median(run_p95s):>14,.1f} "
            f"{statistics.median(run_p99s):>14,.1f}"
        )


def _find_free_port() -> int:
    """Bind an ephemeral TCP port on 127.0.0.1 and return its port number."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


async def benchmark_loopback_websocket_delivery(
    subscriber_counts: list[int],
    polling_intervals_ms: list[int],
    warmup_operations: int = LOOPBACK_WARMUP_OPS,
    num_operations: int = LOOPBACK_MEASURED_OPS,
) -> None:
    """Measure real TCP loopback WebSocket fan-out completion (127.0.0.1).

    Times the full path OrderBook.add_order -> WebSocketConnectionManager ->
    FastAPI/Uvicorn -> 127.0.0.1 TCP -> all aiohttp clients receive JSON
    (i.e. all-subscriber fan-out completion across N clients), after an
    unmeasured warmup phase.
    """
    print("\nReal Loopback WebSocket Delivery (127.0.0.1 TCP, All-Client Fan-Out)")
    print("-" * 82)
    print(
        f"{'Subscribers':>12} {'Operations':>12} "
        f"{'p50 us':>14} {'p95 us':>14} {'p99 us':>14}"
    )

    manager = WebSocketConnectionManager()
    app = FastAPI()

    @app.websocket("/ws/{ticker}")
    async def ws_endpoint(websocket: WebSocket, ticker: str) -> None:
        await websocket.accept()
        conn = manager.subscribe(websocket, ticker)
        try:
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            await manager.unsubscribe(websocket, ticker, connection=conn)

    port = _find_free_port()
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="error",
        access_log=False,
    )
    server = _NoSignalUvicornServer(config)
    server_task = asyncio.create_task(server.serve())

    startup_deadline = time.monotonic() + 10.0
    try:
        while not server.started:
            if server_task.done():
                await server_task
                raise RuntimeError("Uvicorn benchmark server exited before starting.")
            if time.monotonic() >= startup_deadline:
                raise TimeoutError("Timed out waiting for Uvicorn benchmark server.")
            await asyncio.sleep(0.01)

        single_sub_p50_us: float | None = None
        for count in subscriber_counts:
            clients: list[aiohttp.ClientWebSocketResponse] = []
            async with aiohttp.ClientSession() as session:
                try:
                    for _ in range(count):
                        ws = await session.ws_connect(
                            f"ws://127.0.0.1:{port}/ws/{TICKER}",
                            timeout=aiohttp.ClientWSTimeout(ws_receive=10.0),
                        )
                        clients.append(ws)

                    reg_deadline = time.monotonic() + 10.0
                    while len(manager.connections_per_ticker.get(TICKER, {})) < count:
                        if server_task.done():
                            await server_task
                            raise RuntimeError("Uvicorn server exited during connect.")
                        if any(ws.closed for ws in clients):
                            raise RuntimeError(
                                "WebSocket client closed before server registration."
                            )
                        if time.monotonic() >= reg_deadline:
                            raise TimeoutError(
                                "Timed out waiting for WebSocket client registration."
                            )
                        await asyncio.sleep(0.005)

                    book = OrderBook(uuid4(), TICKER)
                    latencies: list[float] = []
                    total_ops = warmup_operations + num_operations

                    for i in range(total_ops):
                        order = {
                            "id": uuid4(),
                            "price": Decimal(100 + i % 50),
                            "quantity": Decimal(100),
                            "side": OrderSide.BUY,
                            "order_type": OrderType.LIMIT,
                            "created_at": datetime.now(UTC),
                        }
                        start = time.perf_counter()
                        book.add_order(order)
                        deltas = book.last_deltas
                        assert len(deltas) == 1
                        message = {
                            "type": "deltas",
                            "data": [_delta_to_json_dict(deltas[0])],
                        }
                        manager.broadcast(message, TICKER)
                        await asyncio.gather(
                            *(ws.receive_json(timeout=10.0) for ws in clients)
                        )
                        elapsed = time.perf_counter() - start
                        if i >= warmup_operations:
                            latencies.append(elapsed * 1_000_000)

                    latencies.sort()
                    p50 = latencies[len(latencies) // 2]
                    p95 = latencies[int(len(latencies) * 0.95)]
                    p99 = latencies[int(len(latencies) * 0.99)]
                    if count == 1:
                        single_sub_p50_us = p50
                    print(
                        f"{count:>12,} {num_operations:>12,} "
                        f"{p50:>14,.1f} {p95:>14,.1f} {p99:>14,.1f}"
                    )
                finally:
                    await asyncio.gather(
                        *(ws.close() for ws in clients), return_exceptions=True
                    )
    finally:
        await manager.close_all()
        server.should_exit = True
        try:
            await asyncio.wait_for(server_task, timeout=5.0)
        except TimeoutError:
            server.force_exit = True
            server_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await server_task

    if single_sub_p50_us is not None:
        push_ms = single_sub_p50_us / 1_000
        print(
            "\nLoopback WebSocket p50 vs Theoretical Average Polling Wait "
            "(interval / 2)"
        )
        print("-" * 82)
        print(
            f"Loopback WebSocket p50 latency (1 subscriber): {single_sub_p50_us:,.1f} us"
        )
        print(f"{'Polling interval':>18} {'Theoretical wait':>20} {'Ratio':>10}")
        for interval_ms in polling_intervals_ms:
            avg_polling_ms = interval_ms / 2
            speedup = avg_polling_ms / push_ms
            print(
                f"{interval_ms:>15,} ms {avg_polling_ms:>17,.1f} ms {speedup:>9,.1f}x"
            )


async def benchmark_snapshot_vs_delta_bandwidth(
    num_operations: int = 200,
) -> None:
    """Measure WebSocket JSON payload size for full snapshots vs deltas."""
    print("\nWebSocket Bandwidth: Snapshot vs Delta Streaming")
    print("-" * 82)

    book = OrderBook(uuid4(), TICKER)
    total_snapshot_bytes = 0
    total_delta_bytes = 0

    for i in range(100):
        book.add_order(
            {
                "id": uuid4(),
                "price": Decimal(100 + i % 20),
                "quantity": Decimal(100),
                "side": OrderSide.BUY,
                "order_type": OrderType.LIMIT,
                "created_at": datetime.now(UTC),
            }
        )
        book.add_order(
            {
                "id": uuid4(),
                "price": Decimal(200 + i % 20),
                "quantity": Decimal(100),
                "side": OrderSide.SELL,
                "order_type": OrderType.LIMIT,
                "created_at": datetime.now(UTC),
            }
        )

    for i in range(num_operations):
        order = {
            "id": uuid4(),
            "price": Decimal(100 + i % 20),
            "quantity": Decimal(50),
            "side": OrderSide.BUY if i % 3 != 0 else OrderSide.SELL,
            "order_type": OrderType.LIMIT,
            "created_at": datetime.now(UTC),
        }
        book.add_order(order)

        snapshot_msg = {
            "type": "snapshot",
            "data": book.get_full_snapshot(),
        }
        total_snapshot_bytes += len(orjson.dumps(snapshot_msg, default=str))

        deltas = book.last_deltas
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
    """Run all WebSocket benchmarks."""
    print("=" * 82)
    print("WebSocket Benchmarks")
    print("=" * 82)
    print(f"Runs per in-process benchmark: {NUM_RUNS} (reporting median)")
    print()

    subscriber_counts = [1, 10, 50, 100, 500, 1_000]
    await benchmark_fan_out_throughput(subscriber_counts)
    await benchmark_push_latency(subscriber_counts)
    await benchmark_loopback_websocket_delivery(
        subscriber_counts=[1, 10, 50],
        polling_intervals_ms=[1, 10, 50, 100, 500, 1_000],
    )
    await benchmark_snapshot_vs_delta_bandwidth()

    print()
    print("=" * 82)
    print("WebSocket benchmarks complete")
    print("=" * 82)


if __name__ == "__main__":
    asyncio.run(main())
