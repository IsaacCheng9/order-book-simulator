from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

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
    price: Decimal = Decimal(100),
    quantity: Decimal = Decimal(50),
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
        quantity=Decimal(25),
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


@pytest.mark.asyncio
async def test_get_deltas_since_with_sequence_falls_back_on_empty_reset_or_tail_gap():
    """Return None to trigger snapshot fallback when delta continuity is broken."""
    stock_id = uuid4()

    # Empty deltas list with current_seq == 0 and client at 0 is up to date.
    deltas, seq = await order_book_cache.get_deltas_since_with_sequence(stock_id, 0)
    assert deltas == []
    assert seq == 0

    # Empty deltas list while sequence watermark advanced must force snapshot.
    await order_book_cache.redis.set(order_book_cache._get_delta_seq_key(stock_id), 5)
    deltas, seq = await order_book_cache.get_deltas_since_with_sequence(stock_id, 2)
    assert deltas is None
    assert seq == 5

    # Client ahead of Redis after a Redis reset must force snapshot.
    await order_book_cache.store_deltas(stock_id, [_make_delta(6), _make_delta(7)])
    deltas, seq = await order_book_cache.get_deltas_since_with_sequence(stock_id, 99)
    assert deltas is None
    assert seq == 7

    # Tail mismatch (seq watermark ahead of latest stored delta) forces snapshot.
    await order_book_cache.redis.set(order_book_cache._get_delta_seq_key(stock_id), 10)
    deltas, seq = await order_book_cache.get_deltas_since_with_sequence(stock_id, 6)
    assert deltas is None
    assert seq == 10

    # Reverse mismatch (deltas ahead of seq watermark) also forces snapshot.
    await order_book_cache.redis.set(order_book_cache._get_delta_seq_key(stock_id), 0)
    deltas, seq = await order_book_cache.get_deltas_since_with_sequence(stock_id, 0)
    assert deltas is None
    assert seq == 0


def _install_pipeline_spy(
    monkeypatch: pytest.MonkeyPatch,
) -> list[list[str]]:
    """Record Redis MULTI/EXEC transaction command lists executed on the cache."""
    transactions: list[list[str]] = []
    redis_client: Any = order_book_cache.redis
    original_pipeline = redis_client.pipeline

    def spy_pipeline(transaction: bool = True) -> Any:
        assert transaction is True
        pipe: Any = original_pipeline(transaction=transaction)
        original_execute = pipe.execute

        async def spy_execute() -> list[Any]:
            transactions.append([op_name for op_name, _ in pipe._ops])
            return await original_execute()

        pipe.execute = spy_execute
        return pipe

    monkeypatch.setattr(redis_client, "pipeline", spy_pipeline)
    return transactions


@pytest.mark.asyncio
async def test_store_order_book_and_deltas_writes_atomically(
    monkeypatch: pytest.MonkeyPatch,
):
    """Persist snapshot, deltas, and sequence watermark in one transaction."""
    stock_id: UUID = uuid4()
    snapshot = {
        "bids": [{"price": "100", "quantity": "50", "order_count": 1}],
        "asks": [],
    }
    deltas = [_make_delta(1), _make_delta(2)]
    transactions = _install_pipeline_spy(monkeypatch)

    await order_book_cache.store_order_book_and_deltas(stock_id, snapshot, deltas)

    # Snapshot, delta append, trim, and sequence update must share one MULTI/EXEC.
    assert transactions == [["set", "rpush", "ltrim", "set"]]

    stored_book, stored_seq = await order_book_cache.get_order_book_with_sequence(
        stock_id
    )
    assert stored_book == snapshot
    assert stored_seq == 2

    # Replaying already-stored deltas must not duplicate entries in Redis.
    await order_book_cache.store_order_book_and_deltas(stock_id, snapshot, deltas)
    stored_deltas = await order_book_cache.get_deltas_since(stock_id, 0)
    assert stored_deltas is not None
    assert [d["sequence_number"] for d in stored_deltas] == [1, 2]


@pytest.mark.asyncio
async def test_replace_order_book_and_deltas_writes_atomically(
    monkeypatch: pytest.MonkeyPatch,
):
    """Replace snapshot, deltas, and sequence watermark in one transaction."""
    stock_id: UUID = uuid4()
    await order_book_cache.store_order_book_and_deltas(
        stock_id,
        {"bids": [{"price": "99", "quantity": "10", "order_count": 1}], "asks": []},
        [_make_delta(1)],
    )

    rebuilt_snapshot = {
        "bids": [{"price": "100", "quantity": "50", "order_count": 1}],
        "asks": [],
    }
    rebuilt_deltas = [_make_delta(1), _make_delta(2), _make_delta(3)]
    transactions = _install_pipeline_spy(monkeypatch)

    await order_book_cache.replace_order_book_and_deltas(
        stock_id, rebuilt_snapshot, rebuilt_deltas, current_sequence=3
    )

    assert transactions == [["set", "delete", "rpush", "set"]]

    stored_book, stored_seq = await order_book_cache.get_order_book_with_sequence(
        stock_id
    )
    assert stored_book == rebuilt_snapshot
    assert stored_seq == 3
    stored_deltas = await order_book_cache.get_deltas_since(stock_id, 0)
    assert stored_deltas is not None
    assert [d["sequence_number"] for d in stored_deltas] == [1, 2, 3]
