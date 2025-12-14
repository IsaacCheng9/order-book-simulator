import time
from decimal import Decimal
from uuid import uuid4
from datetime import datetime, timezone

from order_book_simulator.matching.order_book import OrderBook
from order_book_simulator.common.models import OrderSide, OrderType


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
        create_test_order(Decimal(100 + index % 100), OrderSide.BUY)
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
        create_test_order(price, OrderSide.BUY if index % 2 == 0 else OrderSide.SELL)
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


if __name__ == "__main__":
    benchmark_insertion(10_000)
    benchmark_matching(10_000)
