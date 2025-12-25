"""
Integration benchmark using real Redis and Kafka.

This benchmark measures end-to-end performance with actual I/O operations,
providing realistic production performance metrics. Unlike the unit benchmark
which uses mocks, this shows the true impact of async Redis and other
optimisations.

Requires Docker Compose services to be running.
"""

import asyncio
import time
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from aiokafka import AIOKafkaProducer

from order_book_simulator.common.models import OrderSide, OrderType
from order_book_simulator.matching.matching_engine import MatchingEngine


def create_order(
    stock_id: UUID, price: Decimal, side: OrderSide = OrderSide.BUY
) -> dict[str, Any]:
    """
    Creates a test order with the specified price and side.

    Args:
        stock_id: The stock ID for the order.
        price: The price of the order.
        side: The side of the order.

    Returns:
        A dictionary representing the test order.
    """
    return {
        "id": str(uuid4()),
        "stock_id": str(stock_id),
        "ticker": "TEST",
        "price": price,
        "quantity": Decimal("100"),
        "side": side,
        "type": OrderType.LIMIT,
    }


async def create_engine() -> tuple[MatchingEngine, AIOKafkaProducer]:
    """
    Creates a MatchingEngine with real Redis and Kafka.

    Returns:
        A tuple of (MatchingEngine, AIOKafkaProducer).
    """
    # Use real Redis (sync for now, will be async in the async Redis branch).
    from redis import Redis
    from unittest.mock import AsyncMock

    from order_book_simulator.common.cache import order_book_cache

    redis_client = Redis.from_url("redis://localhost:6379/1")

    # Override the global cache to use localhost instead of redis:6379.
    order_book_cache.redis = redis_client

    # Use real Kafka producer.
    producer = AIOKafkaProducer(
        bootstrap_servers="localhost:9092",
        compression_type="gzip",
    )
    await producer.start()

    # Mock analytics for now (analytics uses async Redis which isn't on this
    # branch yet). On the async Redis branch, use real analytics.
    analytics = AsyncMock()
    analytics.record_state_from_order_book = AsyncMock()

    engine = MatchingEngine(producer, analytics)
    return engine, producer


async def benchmark_insertion(num_orders: int = 1_000) -> float:
    """
    Benchmarks the insertion of buy orders into the matching engine with
    varying prices (no matching occurs).

    Args:
        num_orders: The number of orders to insert.

    Returns:
        The number of orders processed per second.
    """
    engine, producer = await create_engine()
    stock_id = uuid4()

    orders = [
        create_order(stock_id, Decimal(100 + i % 100), OrderSide.BUY)
        for i in range(num_orders)
    ]

    start = time.perf_counter()
    for order in orders:
        await engine.process_order(order)
    elapsed = time.perf_counter() - start

    await producer.stop()

    orders_per_second = num_orders / elapsed
    print(
        f"Inserted {num_orders:,} orders in {elapsed:.3f} seconds "
        f"({orders_per_second:,.2f} orders/second)"
    )
    return orders_per_second


async def benchmark_matching(num_orders: int = 1_000) -> float:
    """
    Benchmarks the matching logic by inserting alternating buy and sell
    orders at the same price.

    Args:
        num_orders: The number of orders to insert.

    Returns:
        The number of orders processed per second.
    """
    engine, producer = await create_engine()
    stock_id = uuid4()
    price = Decimal("100")

    orders = [
        create_order(stock_id, price, OrderSide.BUY if i % 2 == 0 else OrderSide.SELL)
        for i in range(num_orders)
    ]

    start = time.perf_counter()
    for order in orders:
        await engine.process_order(order)
    elapsed = time.perf_counter() - start

    await producer.stop()

    orders_per_second = num_orders / elapsed
    print(
        f"Matched {num_orders:,} orders in {elapsed:.3f} seconds "
        f"({orders_per_second:,.2f} orders/second)"
    )
    return orders_per_second


async def benchmark_deep_book(
    num_levels: int = 100, orders_per_level: int = 10
) -> float:
    """
    Benchmarks the insertion of buy orders into a deep order book (many price
    levels).

    Args:
        num_levels: The number of price levels to insert.
        orders_per_level: The number of orders to insert per price level.

    Returns:
        The number of orders processed per second.
    """
    engine, producer = await create_engine()
    stock_id = uuid4()
    total_orders = num_levels * orders_per_level

    orders = [
        create_order(stock_id, Decimal(100 + i // orders_per_level), OrderSide.BUY)
        for i in range(total_orders)
    ]

    start = time.perf_counter()
    for order in orders:
        await engine.process_order(order)
    elapsed = time.perf_counter() - start

    await producer.stop()

    orders_per_second = total_orders / elapsed
    print(
        f"Inserted {total_orders:,} orders, with {num_levels:,} price levels "
        f"and {orders_per_level:,} orders per level into a deep book "
        f"in {elapsed:.3f} seconds ({orders_per_second:,.2f} orders/second)"
    )
    return orders_per_second


async def main() -> None:
    """Runs all integration benchmarks."""
    print("=" * 80)
    print("Integration Benchmark (Real Redis + Kafka)")
    print("=" * 80)
    print()
    print("Note: These results reflect actual I/O performance and show the")
    print("true impact of async Redis and other optimisations.")
    print()

    await benchmark_insertion(1_000)
    await benchmark_matching(1_000)
    await benchmark_deep_book(100, 10)  # 1,000 orders total


if __name__ == "__main__":
    asyncio.run(main())
