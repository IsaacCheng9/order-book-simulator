from decimal import Decimal

import pytest

from order_book_simulator.common.models import DeltaType, OrderSide
from order_book_simulator.matching.delta_buffer import DeltaBuffer


@pytest.fixture
def buffer() -> DeltaBuffer:
    """Creates a delta buffer with a small max size for testing."""
    return DeltaBuffer(max_size=5)


def test_add_increments_sequence_monotonically(buffer: DeltaBuffer) -> None:
    """Tests that each add call produces a strictly increasing sequence."""
    d1 = buffer.add(
        DeltaType.LEVEL_UPDATE,
        "AAPL",
        OrderSide.BUY,
        Decimal("150"),
        Decimal("100"),
        3,
    )
    d2 = buffer.add(
        DeltaType.LEVEL_UPDATE,
        "AAPL",
        OrderSide.BUY,
        Decimal("150"),
        Decimal("200"),
        5,
    )
    d3 = buffer.add(
        DeltaType.TRADE,
        "AAPL",
        None,
        Decimal("150"),
        Decimal("50"),
    )

    assert d1.sequence_number == 1
    assert d2.sequence_number == 2
    assert d3.sequence_number == 3
    assert buffer.current_sequence == 3


def test_get_deltas_since_returns_newer_deltas(buffer: DeltaBuffer) -> None:
    """Tests that get_deltas_since returns only deltas after the given sequence."""
    buffer.add(
        DeltaType.LEVEL_UPDATE,
        "AAPL",
        OrderSide.BUY,
        Decimal("150"),
        Decimal("100"),
        3,
    )
    buffer.add(
        DeltaType.LEVEL_REMOVE,
        "AAPL",
        OrderSide.SELL,
        Decimal("155"),
        Decimal("0"),
        0,
    )
    buffer.add(
        DeltaType.TRADE,
        "AAPL",
        None,
        Decimal("150"),
        Decimal("50"),
    )

    deltas = buffer.get_deltas_since(1)

    assert deltas is not None
    assert len(deltas) == 2
    assert deltas[0].sequence_number == 2
    assert deltas[0].delta_type == DeltaType.LEVEL_REMOVE
    assert deltas[1].sequence_number == 3
    assert deltas[1].delta_type == DeltaType.TRADE


def test_get_deltas_since_returns_empty_when_up_to_date(
    buffer: DeltaBuffer,
) -> None:
    """Tests that an up-to-date client receives an empty list."""
    buffer.add(
        DeltaType.LEVEL_UPDATE,
        "AAPL",
        OrderSide.BUY,
        Decimal("150"),
        Decimal("100"),
        3,
    )

    assert buffer.get_deltas_since(1) == []
    assert buffer.get_deltas_since(2) == []


def test_get_deltas_since_returns_none_when_evicted(
    buffer: DeltaBuffer,
) -> None:
    """Tests that None is returned when the requested sequence has been
    evicted from the buffer.
    """
    # Fill the buffer beyond capacity (max_size=5).
    for i in range(7):
        buffer.add(
            DeltaType.LEVEL_UPDATE,
            "AAPL",
            OrderSide.BUY,
            Decimal("150"),
            Decimal(i),
            i,
        )

    # Sequence 1 has been evicted - the buffer can't prove continuity.
    assert buffer.get_deltas_since(1) is None
    # Sequence 2 is recoverable - the oldest delta is 3, so 2+1 == 3.
    deltas = buffer.get_deltas_since(2)
    assert deltas is not None
    assert len(deltas) == 5
    assert deltas[0].sequence_number == 3


def test_get_deltas_since_returns_none_when_buffer_empty() -> None:
    """Tests that None is returned for a non-zero sequence when the buffer
    is empty.
    """
    buffer = DeltaBuffer(max_size=5)
    assert buffer.get_deltas_since(0) == []


def test_get_deltas_since_zero_returns_all_deltas(
    buffer: DeltaBuffer,
) -> None:
    """Tests that requesting from sequence 0 returns all buffered deltas."""
    buffer.add(
        DeltaType.LEVEL_UPDATE,
        "AAPL",
        OrderSide.BUY,
        Decimal("150"),
        Decimal("100"),
        3,
    )
    buffer.add(
        DeltaType.TRADE,
        "AAPL",
        None,
        Decimal("150"),
        Decimal("50"),
    )

    deltas = buffer.get_deltas_since(0)

    assert deltas is not None
    assert len(deltas) == 2
    assert deltas[0].sequence_number == 1
    assert deltas[1].sequence_number == 2


def test_current_sequence_starts_at_zero() -> None:
    """Tests that a fresh buffer starts at sequence 0."""
    buffer = DeltaBuffer()
    assert buffer.current_sequence == 0


def test_delta_fields_are_correct(buffer: DeltaBuffer) -> None:
    """Tests that a delta stores all fields correctly."""
    delta = buffer.add(
        DeltaType.LEVEL_UPDATE,
        "MSFT",
        OrderSide.SELL,
        Decimal("300.50"),
        Decimal("75"),
        2,
    )

    assert delta.delta_type == DeltaType.LEVEL_UPDATE
    assert delta.ticker == "MSFT"
    assert delta.side == OrderSide.SELL
    assert delta.price == Decimal("300.50")
    assert delta.quantity == Decimal("75")
    assert delta.order_count == 2
    assert delta.trade_id is None


def test_trade_delta_has_no_side_or_order_count(
    buffer: DeltaBuffer,
) -> None:
    """Tests that trade deltas have None for side and order_count."""
    from uuid import uuid4

    trade_id = uuid4()
    delta = buffer.add(
        DeltaType.TRADE,
        "AAPL",
        None,
        Decimal("150"),
        Decimal("50"),
        trade_id=trade_id,
    )

    assert delta.side is None
    assert delta.order_count is None
    assert delta.trade_id == trade_id
