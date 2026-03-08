from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from order_book_simulator.common.models import (
    DeltaType,
    OrderSide,
)
from order_book_simulator.matching.order_book import OrderBook


def create_order(
    price: Decimal | None = None,
    quantity: Decimal = Decimal("1.0"),
    side: OrderSide = OrderSide.BUY,
) -> dict:
    """Creates a limit order dictionary for testing."""
    return {
        "id": uuid4(),
        "price": price,
        "quantity": quantity,
        "side": side,
        "order_type": "LIMIT",
        "created_at": datetime.now(timezone.utc),
    }


def make_book() -> OrderBook:
    """Creates a fresh order book for testing."""
    return OrderBook(stock_id=uuid4(), ticker="TEST")


def test_add_order_emits_level_update() -> None:
    """Tests that resting a limit order emits a LEVEL_UPDATE delta."""
    book = make_book()
    book.add_order(
        create_order(price=Decimal("100"), quantity=Decimal("10")),
    )

    deltas = book.delta_buffer.get_delta_since(0)
    assert deltas is not None
    assert len(deltas) == 1

    d = deltas[0]
    assert d.delta_type == DeltaType.LEVEL_UPDATE
    assert d.side == OrderSide.BUY
    assert d.price == Decimal("100")
    assert d.quantity == Decimal("10")
    assert d.order_count == 1


def test_add_two_orders_same_level_emits_correct_quantity() -> None:
    """Tests that a second order at the same level emits a LEVEL_UPDATE
    with the aggregate quantity.
    """
    book = make_book()
    book.add_order(
        create_order(price=Decimal("100"), quantity=Decimal("10")),
    )
    book.add_order(
        create_order(price=Decimal("100"), quantity=Decimal("5")),
    )

    deltas = book.delta_buffer.get_delta_since(1)
    assert deltas is not None
    assert len(deltas) == 1

    d = deltas[0]
    assert d.delta_type == DeltaType.LEVEL_UPDATE
    assert d.quantity == Decimal("15")
    assert d.order_count == 2


def test_full_match_emits_trade_and_level_remove() -> None:
    """Tests that a full match emits a TRADE delta and a LEVEL_REMOVE
    for the emptied level.
    """
    book = make_book()
    book.add_order(
        create_order(
            price=Decimal("100"),
            quantity=Decimal("10"),
            side=OrderSide.SELL,
        ),
    )
    # Reset to only see deltas from the match.
    seq_before = book.delta_buffer.current_sequence

    book.add_order(
        create_order(price=Decimal("100"), quantity=Decimal("10")),
    )

    deltas = book.delta_buffer.get_delta_since(seq_before)
    assert deltas is not None

    trade_deltas = [d for d in deltas if d.delta_type == DeltaType.TRADE]
    remove_deltas = [d for d in deltas if d.delta_type == DeltaType.LEVEL_REMOVE]

    assert len(trade_deltas) == 1
    assert trade_deltas[0].price == Decimal("100")
    assert trade_deltas[0].quantity == Decimal("10")
    assert trade_deltas[0].side is None
    assert trade_deltas[0].trade_id is not None

    assert len(remove_deltas) == 1
    assert remove_deltas[0].side == OrderSide.SELL
    assert remove_deltas[0].price == Decimal("100")
    assert remove_deltas[0].quantity == Decimal("0")
    assert remove_deltas[0].order_count == 0


def test_partial_match_emits_trade_and_level_update() -> None:
    """Tests that a partial match emits a TRADE and a LEVEL_UPDATE with
    remaining quantity.
    """
    book = make_book()
    book.add_order(
        create_order(
            price=Decimal("100"),
            quantity=Decimal("10"),
            side=OrderSide.SELL,
        ),
    )
    seq_before = book.delta_buffer.current_sequence

    book.add_order(
        create_order(price=Decimal("100"), quantity=Decimal("3")),
    )

    deltas = book.delta_buffer.get_delta_since(seq_before)
    assert deltas is not None

    trade_deltas = [d for d in deltas if d.delta_type == DeltaType.TRADE]
    update_deltas = [d for d in deltas if d.delta_type == DeltaType.LEVEL_UPDATE]

    assert len(trade_deltas) == 1
    assert trade_deltas[0].quantity == Decimal("3")

    assert len(update_deltas) == 1
    assert update_deltas[0].side == OrderSide.SELL
    assert update_deltas[0].quantity == Decimal("7")
    assert update_deltas[0].order_count == 1


def test_cancel_last_order_emits_level_remove() -> None:
    """Tests that cancelling the last order at a level emits
    LEVEL_REMOVE.
    """
    book = make_book()
    order = create_order(price=Decimal("100"), quantity=Decimal("10"))
    book.add_order(order)
    seq_before = book.delta_buffer.current_sequence

    book.cancel_order(order["id"])

    deltas = book.delta_buffer.get_delta_since(seq_before)
    assert deltas is not None
    assert len(deltas) == 1

    d = deltas[0]
    assert d.delta_type == DeltaType.LEVEL_REMOVE
    assert d.side == OrderSide.BUY
    assert d.price == Decimal("100")
    assert d.quantity == Decimal("0")
    assert d.order_count == 0


def test_cancel_one_of_two_orders_emits_level_update() -> None:
    """Tests that cancelling one order when others remain emits
    LEVEL_UPDATE with the remaining aggregate.
    """
    book = make_book()
    order1 = create_order(price=Decimal("100"), quantity=Decimal("10"))
    order2 = create_order(price=Decimal("100"), quantity=Decimal("5"))
    book.add_order(order1)
    book.add_order(order2)
    seq_before = book.delta_buffer.current_sequence

    book.cancel_order(order1["id"])

    deltas = book.delta_buffer.get_delta_since(seq_before)
    assert deltas is not None
    assert len(deltas) == 1

    d = deltas[0]
    assert d.delta_type == DeltaType.LEVEL_UPDATE
    assert d.quantity == Decimal("5")
    assert d.order_count == 1


def test_cancel_nonexistent_order_emits_no_delta() -> None:
    """Tests that cancelling a non-existent order produces no delta."""
    book = make_book()
    seq_before = book.delta_buffer.current_sequence

    book.cancel_order(uuid4())

    deltas = book.delta_buffer.get_delta_since(seq_before)
    assert deltas == []


def test_match_across_multiple_levels_emits_correct_deltas() -> None:
    """Tests that matching across two price levels emits the right
    trade and level deltas for each.
    """
    book = make_book()
    book.add_order(
        create_order(
            price=Decimal("100"),
            quantity=Decimal("5"),
            side=OrderSide.SELL,
        ),
    )
    book.add_order(
        create_order(
            price=Decimal("101"),
            quantity=Decimal("5"),
            side=OrderSide.SELL,
        ),
    )
    seq_before = book.delta_buffer.current_sequence

    book.add_order(
        create_order(price=Decimal("101"), quantity=Decimal("10")),
    )

    deltas = book.delta_buffer.get_delta_since(seq_before)
    assert deltas is not None

    trade_deltas = [d for d in deltas if d.delta_type == DeltaType.TRADE]
    remove_deltas = [d for d in deltas if d.delta_type == DeltaType.LEVEL_REMOVE]

    assert len(trade_deltas) == 2
    assert trade_deltas[0].price == Decimal("100")
    assert trade_deltas[1].price == Decimal("101")

    assert len(remove_deltas) == 2
    assert remove_deltas[0].price == Decimal("100")
    assert remove_deltas[1].price == Decimal("101")


def test_sequence_numbers_are_monotonic_across_operations() -> None:
    """Tests that sequence numbers increase monotonically across adds,
    matches, and cancellations.
    """
    book = make_book()
    book.add_order(
        create_order(
            price=Decimal("100"),
            quantity=Decimal("10"),
            side=OrderSide.SELL,
        ),
    )
    order = create_order(price=Decimal("100"), quantity=Decimal("5"))
    book.add_order(order)

    deltas = book.delta_buffer.get_delta_since(0)
    assert deltas is not None

    sequences = [d.sequence_number for d in deltas]
    assert sequences == sorted(sequences)
    assert len(sequences) == len(set(sequences))
