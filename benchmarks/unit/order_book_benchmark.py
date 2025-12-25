"""
A benchmark harness for the order book.

The order book benchmark measures the raw performance of the matching logic and
data structures, so it will demonstrate a higher throughput.

This can be used to measure the performance of the order book under various
conditions and before / after changes to the code implementation.
"""

import time
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from order_book_simulator.common.models import OrderSide, OrderType
from order_book_simulator.matching.order_book import OrderBook


def create_test_order(price: Decimal, side: OrderSide = OrderSide.BUY) -> dict:
    """
    Creates a test order with the specified price and side.

    Args:
        price: The price of the order.
        side: The side of the order.

    Returns:
        A dictionary representing the test order.
    """
    return {
        "id": uuid4(),
        "price": price,
        "quantity": Decimal("100"),
        "side": side,
        "type": OrderType.LIMIT,
        "created_at": datetime.now(timezone.utc),
    }


def benchmark_insertion(num_orders: int = 10_000) -> float:
    """
    Benchmarks the insertion of buy orders into the order book with varying
    prices.

    Args:
        num_orders: The number of orders to insert.

    Returns:
        The number of orders processed per second.
    """
    book = OrderBook(uuid4())
    orders = [
        create_test_order(
            Decimal(100 + index % 100),
            OrderSide.BUY,
        )
        for index in range(num_orders)
    ]

    start = time.perf_counter()
    for order in orders:
        book.add_order(order)
    end = time.perf_counter()

    elapsed = end - start
    orders_per_second = num_orders / elapsed
    print(
        f"Inserted {num_orders:,} orders in {elapsed:.3f} seconds "
        f"({orders_per_second:,.2f} orders/second)"
    )
    return orders_per_second


def benchmark_matching(num_orders: int = 10_000) -> float:
    """
    Benchmarks the matching logic of the order book by inserting matching buy
    and sell orders into the order book.

    Args:
        num_orders: The number of orders to insert.

    Returns:
        The number of orders processed per second.
    """
    book = OrderBook(uuid4())
    price = Decimal(100)
    # Create alternating buy and sell orders so they match against each other.
    orders = [
        create_test_order(
            price,
            OrderSide.BUY if index % 2 == 0 else OrderSide.SELL,
        )
        for index in range(num_orders)
    ]

    start = time.perf_counter()
    for order in orders:
        book.add_order(order)
    end = time.perf_counter()

    elapsed = end - start
    orders_per_second = num_orders / elapsed
    print(
        f"Matched {num_orders:,} orders in {elapsed:.3f} seconds "
        f"({orders_per_second:,.2f} orders/second)"
    )
    return orders_per_second


def benchmark_deep_book(num_levels: int = 1000, orders_per_level: int = 100) -> float:
    """
    Benchmarks the insertion of buy orders into a deep order book (many price
    levels).

    Args:
        num_levels: The number of price levels to insert.
        orders_per_level: The number of orders to insert per price level.

    Returns:
        The number of orders processed per second.
    """
    book = OrderBook(uuid4())
    total_orders = num_levels * orders_per_level
    orders = [
        create_test_order(
            Decimal(100 + index // orders_per_level),
            OrderSide.BUY,
        )
        for index in range(total_orders)
    ]

    start = time.perf_counter()
    for order in orders:
        book.add_order(order)
    end = time.perf_counter()
    elapsed = end - start

    orders_per_second = total_orders / elapsed
    print(
        f"Inserted {total_orders:,} orders, with {num_levels:,} price levels "
        f"and {orders_per_level:,} orders per level into a deep book "
        f"in {elapsed:.3f} seconds ({orders_per_second:,.2f} orders/second)"
    )
    return orders_per_second


def main() -> None:
    """Runs all benchmarks."""
    benchmark_insertion(10_000)
    benchmark_matching(10_000)
    benchmark_deep_book(1_000, 100)


if __name__ == "__main__":
    main()
