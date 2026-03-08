"""
Benchmark comparing full snapshot vs delta payload sizes.

Runs a realistic order book workload (adds, matches, cancellations) and
measures the total bytes transmitted under each approach to quantify the
bandwidth reduction from delta publishing.
"""

from dataclasses import asdict
from decimal import Decimal
from uuid import uuid4

import orjson

from order_book_simulator.common.models import OrderSide, OrderType
from order_book_simulator.matching.order_book import OrderBook


def create_order(
    price: Decimal,
    quantity: Decimal = Decimal("100"),
    side: OrderSide = OrderSide.BUY,
) -> dict:
    """Create a test limit order."""
    from datetime import datetime, timezone

    return {
        "id": uuid4(),
        "price": price,
        "quantity": quantity,
        "side": side,
        "order_type": OrderType.LIMIT,
        "created_at": datetime.now(timezone.utc),
    }


def run_benchmark() -> None:
    """
    Run a mixed workload and compare snapshot vs delta payload sizes.

    The workload builds a realistic book with multiple price levels,
    then performs partial matches and cancellations to exercise all
    delta types (LEVEL_UPDATE, LEVEL_REMOVE, TRADE).
    """
    book = OrderBook(uuid4(), "BENCH")

    total_snapshot_bytes = 0
    total_delta_bytes = 0
    num_operations = 0

    def measure_operation() -> None:
        """Measure payload sizes after the most recent operation."""
        nonlocal total_snapshot_bytes, total_delta_bytes, num_operations

        snapshot_bytes = len(orjson.dumps(book.get_full_snapshot(), default=str))
        total_snapshot_bytes += snapshot_bytes

        deltas = book.delta_buffer.get_deltas_since(seq_before)
        if deltas:
            delta_bytes = sum(len(orjson.dumps(asdict(d), default=str)) for d in deltas)
        else:
            delta_bytes = 0
        total_delta_bytes += delta_bytes
        num_operations += 1

    # Phase 1: Build up the book with resting orders at 50 price levels
    # on each side (100 levels total, 5 orders per level).
    print("Phase 1: Building order book (500 orders, 50 levels per side)")
    resting_orders: list[dict] = []
    for i in range(50):
        for _ in range(5):
            bid = create_order(Decimal(100 + i), Decimal("100"), OrderSide.BUY)
            ask = create_order(Decimal(200 + i), Decimal("100"), OrderSide.SELL)
            resting_orders.extend([bid, ask])

            seq_before = book.delta_buffer.current_sequence
            book.add_order(bid)
            measure_operation()

            seq_before = book.delta_buffer.current_sequence
            book.add_order(ask)
            measure_operation()

    # Phase 2: Partial matches - buy orders that partially fill the
    # lowest ask levels.
    print("Phase 2: Partial matches (100 orders)")
    for i in range(100):
        order = create_order(Decimal(200 + i % 10), Decimal("30"), OrderSide.BUY)
        seq_before = book.delta_buffer.current_sequence
        book.add_order(order)
        measure_operation()

    # Phase 3: Full matches - large buy orders that sweep multiple
    # ask levels.
    print("Phase 3: Full matches (20 aggressive orders)")
    for i in range(20):
        order = create_order(Decimal(250), Decimal("500"), OrderSide.BUY)
        seq_before = book.delta_buffer.current_sequence
        book.add_order(order)
        measure_operation()

    # Phase 4: Cancellations - cancel some resting orders.
    print("Phase 4: Cancellations (100 orders)")
    cancelled = 0
    for order in resting_orders:
        if cancelled >= 100:
            break
        seq_before = book.delta_buffer.current_sequence
        if book.cancel_order(order["id"]):
            measure_operation()
            cancelled += 1

    # Results.
    print("\n" + "=" * 60)
    print("RESULTS")
    print("=" * 60)
    print(f"Total operations:        {num_operations:,}")
    print(f"Snapshot total bytes:     {total_snapshot_bytes:,}")
    print(f"Delta total bytes:        {total_delta_bytes:,}")
    reduction = (1 - total_delta_bytes / total_snapshot_bytes) * 100
    print(f"Bandwidth reduction:      {reduction:.1f}%")
    print(
        f"Avg snapshot per update:  {total_snapshot_bytes / num_operations:,.0f} bytes"
    )
    print(f"Avg delta per update:     {total_delta_bytes / num_operations:,.0f} bytes")


if __name__ == "__main__":
    run_benchmark()
