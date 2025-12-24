"""
A benchmark harness for the matching engine.

This can be used to measure the performance of the matching engine under
various conditions and before / after changes to the code implementation.
"""

import asyncio
import time
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from order_book_simulator.common.cache import order_book_cache
from order_book_simulator.common.models import OrderSide, OrderType
from order_book_simulator.matching.matching_engine import MatchingEngine


def create_order(stock_id, price: Decimal, side: OrderSide) -> dict:
    """Creates a test order."""
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


async def benchmark_engine(num_orders: int = 10_000) -> float:
    """Benchmarks MatchingEngine.process_order() throughput."""
    # Mock Redis (lazy init means we can set it before first use).
    order_book_cache.redis = MagicMock()

    # Mock Kafka producer and analytics (no real I/O).
    mock_producer = AsyncMock()
    mock_producer.send_and_wait = AsyncMock()
    mock_analytics = AsyncMock()
    mock_analytics.record_state_from_order_book = AsyncMock()

    engine = MatchingEngine(mock_producer, mock_analytics)
    stock_id = uuid4()

    # Alternating buy/sell to trigger matching.
    orders = [
        create_order(
            stock_id, Decimal("100"), OrderSide.BUY if i % 2 == 0 else OrderSide.SELL
        )
        for i in range(num_orders)
    ]

    start = time.perf_counter()
    for order in orders:
        await engine.process_order(order)
    elapsed = time.perf_counter() - start

    print(
        f"Processed {num_orders:,} orders in {elapsed:.3f}s "
        f"({num_orders / elapsed:,.0f} orders/sec)"
    )
    return num_orders / elapsed


if __name__ == "__main__":
    asyncio.run(benchmark_engine(10_000))
