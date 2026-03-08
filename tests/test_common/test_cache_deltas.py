from decimal import Decimal
from uuid import uuid4

import pytest

from order_book_simulator.common.cache import order_book_cache
from order_book_simulator.common.models import (
    Delta,
    DeltaType,
    OrderSide,
)


def _make_delta(
    seq: int,
    delta_type: DeltaType = DeltaType.LEVEL_UPDATE,
    side: OrderSide = OrderSide.BUY,
    price: Decimal = Decimal("100"),
    quantity: Decimal = Decimal("50"),
) -> Delta:
    """Creates a Delta with the given sequence number."""
    return Delta(
        sequence_number=seq,
        timestamp=1000.0 + seq,
        delta_type=delta_type,
        ticker="TEST",
        side=side,
        price=price,
        quantity=quantity,
        order_count=1,
    )


@pytest.mark.asyncio
async def test_store_and_retrieve_deltas():
    """Tests that stored deltas can be retrieved via get_deltas_since."""
    stock_id = uuid4()
    deltas = [_make_delta(1), _make_delta(2), _make_delta(3)]
    await order_book_cache.store_deltas(stock_id, deltas)

    result = await order_book_cache.get_deltas_since(stock_id, 0)
    assert result is not None
    assert len(result) == 3
    assert result[0]["sequence_number"] == 1
    assert result[2]["sequence_number"] == 3


@pytest.mark.asyncio
async def test_store_deltas_empty_list_is_noop():
    """Tests that storing an empty delta list does nothing."""
    stock_id = uuid4()
    await order_book_cache.store_deltas(stock_id, [])

    result = await order_book_cache.get_deltas_since(stock_id, 0)
    # No deltas stored, so should return empty list.
    assert result == []


@pytest.mark.asyncio
async def test_store_deltas_updates_sequence_number():
    """Tests that the current sequence number is updated after storing."""
    stock_id = uuid4()
    deltas = [_make_delta(5), _make_delta(6)]
    await order_book_cache.store_deltas(stock_id, deltas)

    seq = await order_book_cache.get_current_delta_sequence_number(stock_id)
    assert seq == 6


@pytest.mark.asyncio
async def test_get_deltas_since_filters_by_sequence():
    """Tests that only deltas newer than the given sequence are returned."""
    stock_id = uuid4()
    deltas = [_make_delta(1), _make_delta(2), _make_delta(3)]
    await order_book_cache.store_deltas(stock_id, deltas)

    result = await order_book_cache.get_deltas_since(stock_id, 2)
    assert result is not None
    assert len(result) == 1
    assert result[0]["sequence_number"] == 3


@pytest.mark.asyncio
async def test_get_deltas_since_returns_empty_when_up_to_date():
    """Tests that an empty list is returned when the client is current."""
    stock_id = uuid4()
    deltas = [_make_delta(1), _make_delta(2)]
    await order_book_cache.store_deltas(stock_id, deltas)

    result = await order_book_cache.get_deltas_since(stock_id, 2)
    assert result == []


@pytest.mark.asyncio
async def test_get_deltas_since_returns_none_when_evicted():
    """
    Tests that None is returned when the requested sequence has been
    evicted.
    """
    stock_id = uuid4()
    # Oldest delta starts at seq 10 - requesting seq 5 is too old.
    deltas = [_make_delta(10), _make_delta(11)]
    await order_book_cache.store_deltas(stock_id, deltas)

    result = await order_book_cache.get_deltas_since(stock_id, 5)
    assert result is None


@pytest.mark.asyncio
async def test_get_deltas_since_no_deltas_stored():
    """Tests that an empty list is returned when no deltas exist."""
    stock_id = uuid4()
    result = await order_book_cache.get_deltas_since(stock_id, 0)
    assert result == []


@pytest.mark.asyncio
async def test_get_current_delta_sequence_number_no_deltas():
    """Tests that 0 is returned when no deltas have been stored."""
    stock_id = uuid4()
    seq = await order_book_cache.get_current_delta_sequence_number(stock_id)
    assert seq == 0


@pytest.mark.asyncio
async def test_store_deltas_preserves_trade_fields():
    """Tests that trade delta fields are correctly serialised and retrieved."""
    stock_id = uuid4()
    trade_id = uuid4()
    delta = Delta(
        sequence_number=1,
        timestamp=1000.0,
        delta_type=DeltaType.TRADE,
        ticker="TEST",
        side=None,
        price=Decimal("150.50"),
        quantity=Decimal("25"),
        trade_id=trade_id,
    )
    await order_book_cache.store_deltas(stock_id, [delta])

    result = await order_book_cache.get_deltas_since(stock_id, 0)
    assert result is not None
    assert len(result) == 1
    assert result[0]["delta_type"] == "TRADE"
    assert result[0]["trade_id"] == str(trade_id)
    assert result[0]["side"] is None
    assert result[0]["price"] == "150.50"
