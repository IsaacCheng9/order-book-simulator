"""
A benchmark harness for the matching engine.

This can be used to measure the performance of the matching engine under
various conditions and before / after changes to the code implementation.
"""

import asyncio
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

from order_book_simulator.common.cache import order_book_cache
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
        "created_at": datetime.now(timezone.utc),
    }


def create_engine() -> MatchingEngine:
    """Creates a MatchingEngine with mocked dependencies."""
    # Mock Redis (lazy init means we can set it before first use).
    order_book_cache.redis = AsyncMock()

    # Mock Kafka producer and analytics (no real I/O).
    mock_producer = AsyncMock()
    mock_producer.send_and_wait = AsyncMock()
    mock_analytics = AsyncMock()
    mock_analytics.record_state_from_order_book = AsyncMock()

    return MatchingEngine(mock_producer, mock_analytics)


async def benchmark_insertion(num_orders: int = 10_000) -> float:
    """
    Benchmarks the insertion of buy orders into the matching engine with
    varying prices (no matching occurs).

    Args:
        num_orders: The number of orders to insert.

    Returns:
        The number of orders processed per second.
    """
    engine = create_engine()
    stock_id = uuid4()

    orders = [
        create_order(stock_id, Decimal(100 + i % 100), OrderSide.BUY)
        for i in range(num_orders)
    ]

    start = time.perf_counter()
    for order in orders:
        await engine.process_order(order)
    elapsed = time.perf_counter() - start

    orders_per_second = num_orders / elapsed
    print(
        f"Inserted {num_orders:,} orders in {elapsed:.3f} seconds "
        f"({orders_per_second:,.2f} orders/second)"
    )
    return orders_per_second


async def benchmark_matching(num_orders: int = 10_000) -> float:
    """
    Benchmarks the matching logic by inserting alternating buy and sell
    orders at the same price.

    Args:
        num_orders: The number of orders to insert.

    Returns:
        The number of orders processed per second.
    """
    engine = create_engine()
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

    orders_per_second = num_orders / elapsed
    print(
        f"Matched {num_orders:,} orders in {elapsed:.3f} seconds "
        f"({orders_per_second:,.2f} orders/second)"
    )
    return orders_per_second


async def benchmark_deep_book(
    num_levels: int = 1_000, orders_per_level: int = 100
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
    engine = create_engine()
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

    orders_per_second = total_orders / elapsed
    print(
        f"Inserted {total_orders:,} orders, with {num_levels:,} price levels "
        f"and {orders_per_level:,} orders per level into a deep book "
        f"in {elapsed:.3f} seconds ({orders_per_second:,.2f} orders/second)"
    )
    return orders_per_second


async def main() -> None:
    """Runs all benchmarks."""
    await benchmark_insertion(5_000)
    await benchmark_matching(5_000)
    await benchmark_deep_book(100, 50)  # 5,000 orders total


if __name__ == "__main__":
    asyncio.run(main())
