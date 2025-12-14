import time
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from order_book_simulator.common.models import OrderSide, OrderType
from order_book_simulator.matching.order_book import OrderBook


def create_test_order(price: Decimal, side: OrderSide = OrderSide.BUY) -> dict:
    return {
        "id": uuid4(),
        "price": price,
        "quantity": Decimal("100"),
        "side": side,
        "type": OrderType.LIMIT,
        "created_at": datetime.now(timezone.utc),
    }


def benchmark_insertion(num_orders: int = 10_000) -> float:
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


if __name__ == "__main__":
    benchmark_insertion(10_000)
    benchmark_matching(10_000)
    benchmark_deep_book(1_000, 100)
