from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, ClassVar
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import orjson
import pytest
from aiokafka import ConsumerRecord, TopicPartition

from order_book_simulator.common.cache import order_book_cache
from order_book_simulator.market_data.analytics import MarketDataAnalytics
from order_book_simulator.matching import order_consumer as order_consumer_module
from order_book_simulator.matching.matching_engine import MatchingEngine
from order_book_simulator.matching.order_consumer import OrderConsumer


class InMemoryKafkaCluster:
    """In-memory single-partition Kafka log with durable committed offsets."""

    def __init__(self, topic: str = "orders") -> None:
        self.tp = TopicPartition(topic, 0)
        self.records: list[ConsumerRecord] = []
        self.committed_offset: int | None = None

    def append(self, payload: dict[str, Any]) -> ConsumerRecord:
        """Append a JSON record to the partition log and return it."""
        offset = len(self.records)
        record = ConsumerRecord(
            topic=self.tp.topic,
            partition=self.tp.partition,
            offset=offset,
            timestamp=offset * 1000,
            timestamp_type=0,
            key=None,
            value=orjson.dumps(payload),
            checksum=None,
            serialized_key_size=-1,
            serialized_value_size=-1,
            headers=(),
        )
        self.records.append(record)
        return record


class ReplayableKafkaConsumer:
    """Fake AIOKafkaConsumer supporting seek, committed, and replay reads."""

    cluster: ClassVar[InMemoryKafkaCluster | None] = None
    stop_at_offset: ClassVar[int | None] = None

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        assert self.__class__.cluster is not None
        self._cluster = self.__class__.cluster
        self._stop_at_offset = self.__class__.stop_at_offset
        self._cursor = 0
        self.started = False
        self.stopped = False

    async def start(self) -> None:
        """Start consuming from the committed offset or beginning."""
        self.started = True
        self._cursor = self._cluster.committed_offset or 0

    async def stop(self) -> None:
        """Mark the consumer as stopped."""
        self.stopped = True

    def assignment(self) -> set[TopicPartition]:
        """Return the assigned partition set."""
        return {self._cluster.tp}

    async def committed(self, tp: TopicPartition) -> int | None:
        """Return the last committed offset for the partition."""
        assert tp == self._cluster.tp
        return self._cluster.committed_offset

    async def beginning_offsets(
        self, partitions: list[TopicPartition]
    ) -> dict[TopicPartition, int]:
        """Return the earliest retained offset for each partition."""
        return dict.fromkeys(partitions, 0)

    def seek(self, tp: TopicPartition, offset: int) -> None:
        """Move the fetch cursor to an explicit offset."""
        assert tp == self._cluster.tp
        self._cursor = offset

    async def position(self, tp: TopicPartition) -> int:
        """Return the current fetch cursor position."""
        assert tp == self._cluster.tp
        return self._cursor

    async def getmany(
        self,
        *partitions: TopicPartition,
        timeout_ms: int = 0,
        max_records: int | None = None,
    ) -> dict[TopicPartition, list[ConsumerRecord]]:
        """Fetch a bounded batch from the current cursor."""
        del timeout_ms
        tp = partitions[0] if partitions else self._cluster.tp
        limit = max_records or 2
        end = min(len(self._cluster.records), self._cursor + limit)
        batch = self._cluster.records[self._cursor : end]
        self._cursor = end
        return {tp: batch}

    async def commit(self) -> None:
        """Commit the current cursor offset."""
        self._cluster.committed_offset = self._cursor

    def __aiter__(self) -> ReplayableKafkaConsumer:
        return self

    async def __anext__(self) -> ConsumerRecord:
        upper_bound = (
            len(self._cluster.records)
            if self._stop_at_offset is None
            else min(self._stop_at_offset, len(self._cluster.records))
        )
        if self._cursor >= upper_bound:
            raise StopAsyncIteration
        record = self._cluster.records[self._cursor]
        self._cursor += 1
        return record


def _build_order_log(
    aapl_id: UUID,
    msft_id: UUID,
) -> list[dict[str, Any]]:
    """Build a deterministic multi-stock log exercising matching and cancels."""
    base = datetime(2026, 7, 26, 12, 0, 0, tzinfo=UTC)
    sell_1_id = str(uuid4())
    sell_2_id = str(uuid4())
    sell_3_id = str(uuid4())
    msft_bid_id = str(uuid4())
    cross_buy_id = str(uuid4())

    return [
        # Offset 0: MSFT resting bid (stays resting across restart).
        {
            "type": "order",
            "id": msft_bid_id,
            "stock_id": str(msft_id),
            "ticker": "MSFT",
            "side": "BUY",
            "order_type": "LIMIT",
            "price": "200.00",
            "quantity": "15",
            "gateway_received_at": (base + timedelta(seconds=1)).isoformat(),
        },
        # Offset 1: AAPL FIFO sell #1 at 100.
        {
            "type": "order",
            "id": sell_1_id,
            "stock_id": str(aapl_id),
            "ticker": "AAPL",
            "side": "SELL",
            "order_type": "LIMIT",
            "price": "100.00",
            "quantity": "10",
            "gateway_received_at": (base + timedelta(seconds=2)).isoformat(),
        },
        # Offset 2: AAPL FIFO sell #2 at 100 (second in queue).
        {
            "type": "order",
            "id": sell_2_id,
            "stock_id": str(aapl_id),
            "ticker": "AAPL",
            "side": "SELL",
            "order_type": "LIMIT",
            "price": "100.00",
            "quantity": "10",
            "gateway_received_at": (base + timedelta(seconds=3)).isoformat(),
        },
        # Offset 3: AAPL resting sell #3 at 101 (cancelled at offset 5).
        {
            "type": "order",
            "id": sell_3_id,
            "stock_id": str(aapl_id),
            "ticker": "AAPL",
            "side": "SELL",
            "order_type": "LIMIT",
            "price": "101.00",
            "quantity": "5",
            "gateway_received_at": (base + timedelta(seconds=4)).isoformat(),
        },
        # Offset 4: AAPL crossing buy that fills sell #1 and partially fills
        # sell #2 (crash target for downstream failure tests).
        {
            "type": "order",
            "id": cross_buy_id,
            "stock_id": str(aapl_id),
            "ticker": "AAPL",
            "side": "BUY",
            "order_type": "LIMIT",
            "price": "100.00",
            "quantity": "14",
            "gateway_received_at": (base + timedelta(seconds=5)).isoformat(),
        },
        # Offset 5: Cancel resting sell #3.
        {
            "type": "cancel",
            "order_id": sell_3_id,
            "stock_id": str(aapl_id),
            "ticker": "AAPL",
            "cancelled_at": (base + timedelta(seconds=6)).isoformat(),
        },
        # Offset 6: Final AAPL bid resting below the remaining ask.
        {
            "type": "order",
            "id": str(uuid4()),
            "stock_id": str(aapl_id),
            "ticker": "AAPL",
            "side": "BUY",
            "order_type": "LIMIT",
            "price": "99.50",
            "quantity": "8",
            "gateway_received_at": (base + timedelta(seconds=7)).isoformat(),
        },
    ]


async def _capture_state(
    engine: MatchingEngine,
    stock_ids: list[UUID],
) -> dict[str, Any]:
    """Capture in-memory book state, Redis caches, and analytics streams."""
    state: dict[str, Any] = {}
    for stock_id in stock_ids:
        book = engine.order_books[stock_id]
        resting_bids = {
            str(price): [(order.id, order.quantity) for order in level.orders.values()]
            for price, level in book.bid_levels.items()
        }
        resting_asks = {
            str(price): [(order.id, order.quantity) for order in level.orders.values()]
            for price, level in book.ask_levels.items()
        }
        stream_entries = await order_book_cache.redis.xrange(
            f"market_data:{stock_id}", min="-", max="+"
        )
        state[str(stock_id)] = {
            "resting_bids": resting_bids,
            "resting_asks": resting_asks,
            "current_seq": book.delta_buffer.current_sequence,
            "snapshot": await order_book_cache.get_order_book(stock_id),
            "redis_seq": await order_book_cache.get_current_delta_sequence_number(
                stock_id
            ),
            "deltas": await order_book_cache.get_deltas_since(stock_id, 0),
            "trades": await order_book_cache.get_trades(stock_id, limit=100),
            "analytics_seqs": [entry[1]["sequence_number"] for entry in stream_entries],
        }
    return state


async def _wipe_redis_keys(stock_ids: list[UUID]) -> None:
    """Clear all Redis keys for the given stocks."""
    keys: list[str] = []
    for stock_id in stock_ids:
        keys.extend(
            [
                f"order_book:{stock_id}",
                f"deltas:{stock_id}",
                f"delta_seq:{stock_id}",
                f"trades:{stock_id}",
                f"market_data:{stock_id}",
            ]
        )
    await order_book_cache.redis.delete(*keys)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "crash_point",
    [
        "after_store_order_book_and_deltas",
        "after_append_trades",
        "after_kafka_publish",
        "before_commit",
    ],
)
async def test_crash_and_restart_matches_uninterrupted_run(
    crash_point: str,
    monkeypatch: pytest.MonkeyPatch,
):
    """Recover identically after a crash at every downstream write boundary."""
    aapl_id = uuid4()
    msft_id = uuid4()
    stock_ids = [aapl_id, msft_id]
    payloads = _build_order_log(aapl_id, msft_id)

    monkeypatch.setattr(
        order_consumer_module,
        "AIOKafkaConsumer",
        ReplayableKafkaConsumer,
    )

    # 1. Baseline uninterrupted run.
    baseline_cluster = InMemoryKafkaCluster()
    for payload in payloads:
        baseline_cluster.append(payload)
    ReplayableKafkaConsumer.cluster = baseline_cluster
    ReplayableKafkaConsumer.stop_at_offset = None

    baseline_producer = AsyncMock()
    baseline_analytics = MarketDataAnalytics(order_book_cache.redis)
    baseline_engine = MatchingEngine(baseline_producer, baseline_analytics)
    baseline_consumer = OrderConsumer(matching_engine=baseline_engine)
    await baseline_consumer.start()
    expected_state = await _capture_state(baseline_engine, stock_ids)

    # Sanity check: offset 4 produced two fills (10 against sell #1, 4 against
    # sell #2) and left 6 remaining on sell #2.
    assert len(expected_state[str(aapl_id)]["trades"]) == 2
    assert expected_state[str(aapl_id)]["resting_asks"] == {
        "100.00": [(UUID(payloads[2]["id"]), Decimal(6))]
    }

    # 2. Reset Redis and run with an injected crash while processing offset 4.
    await _wipe_redis_keys(stock_ids)
    crash_cluster = InMemoryKafkaCluster()
    for payload in payloads:
        crash_cluster.append(payload)
    ReplayableKafkaConsumer.cluster = crash_cluster
    ReplayableKafkaConsumer.stop_at_offset = None

    crash_producer = AsyncMock()
    crash_analytics = MarketDataAnalytics(order_book_cache.redis)
    crash_engine = MatchingEngine(crash_producer, crash_analytics)
    crash_consumer = OrderConsumer(matching_engine=crash_engine)

    # Inject the failure on the crossing buy order (5th order overall, offset 4).
    aapl_calls = {"store_order_book_and_deltas": 0, "append_trades": 0}
    original_store_book_and_deltas = order_book_cache.store_order_book_and_deltas
    original_append_trades = order_book_cache.append_trades
    original_record_analytics = crash_analytics.record_state_from_order_book

    async def failing_store_book_and_deltas(
        stock_id: UUID, snapshot: dict[str, Any], deltas: list[Any]
    ) -> None:
        await original_store_book_and_deltas(stock_id, snapshot, deltas)
        if stock_id == aapl_id:
            aapl_calls["store_order_book_and_deltas"] += 1
            if (
                crash_point == "after_store_order_book_and_deltas"
                and aapl_calls["store_order_book_and_deltas"] == 4
            ):
                raise RuntimeError("injected crash after store_order_book_and_deltas")

    async def failing_append_trades(stock_id: UUID, trades: list[dict]) -> None:
        await original_append_trades(stock_id, trades)
        if stock_id == aapl_id:
            aapl_calls["append_trades"] += 1
            if (
                crash_point == "after_append_trades"
                and aapl_calls["append_trades"] == 1
            ):
                raise RuntimeError("injected crash after append_trades")

    if crash_point == "after_store_order_book_and_deltas":
        monkeypatch.setattr(
            order_book_cache,
            "store_order_book_and_deltas",
            failing_store_book_and_deltas,
        )
    elif crash_point == "after_append_trades":
        monkeypatch.setattr(order_book_cache, "append_trades", failing_append_trades)
    elif crash_point == "after_kafka_publish":
        publish_count = {"n": 0}

        async def failing_send_and_wait(*args: Any, **kwargs: Any) -> None:
            del args, kwargs
            publish_count["n"] += 1
            if publish_count["n"] == 5:
                raise RuntimeError("injected crash after kafka publish")

        crash_producer.send_and_wait.side_effect = failing_send_and_wait
    elif crash_point == "before_commit":
        analytics_count = {"n": 0}

        async def failing_analytics(*args: Any, **kwargs: Any) -> None:
            await original_record_analytics(*args, **kwargs)
            analytics_count["n"] += 1
            if analytics_count["n"] == 5:
                raise RuntimeError("injected crash before commit")

        monkeypatch.setattr(
            crash_analytics, "record_state_from_order_book", failing_analytics
        )

    with pytest.raises(RuntimeError, match="injected crash"):
        await crash_consumer.start()

    # Offsets 0..3 were committed; offset 4 stayed uncommitted.
    assert crash_cluster.committed_offset == 4

    # 3. Restore normal cache methods and restart with a brand-new engine.
    monkeypatch.setattr(
        order_book_cache,
        "store_order_book_and_deltas",
        original_store_book_and_deltas,
    )
    monkeypatch.setattr(order_book_cache, "append_trades", original_append_trades)

    recovered_producer = AsyncMock()
    recovered_analytics = MarketDataAnalytics(order_book_cache.redis)
    recovered_engine = MatchingEngine(recovered_producer, recovered_analytics)
    recovered_consumer = OrderConsumer(matching_engine=recovered_engine)
    await recovered_consumer.start()

    assert crash_cluster.committed_offset == len(payloads)
    # Only the 3 uncommitted messages (offsets 4, 5, 6) should have emitted
    # Kafka market-data messages after recovery.
    assert recovered_producer.send_and_wait.call_count == 3

    recovered_state = await _capture_state(recovered_engine, stock_ids)
    assert recovered_state == expected_state


@pytest.mark.asyncio
async def test_empty_redis_restart_rebuilds_cache_for_quiet_stocks(
    monkeypatch: pytest.MonkeyPatch,
):
    """Restore Redis snapshot and delta history even when Redis restarts empty."""
    aapl_id = uuid4()
    msft_id = uuid4()
    stock_ids = [aapl_id, msft_id]
    payloads = _build_order_log(aapl_id, msft_id)

    monkeypatch.setattr(
        order_consumer_module,
        "AIOKafkaConsumer",
        ReplayableKafkaConsumer,
    )

    cluster = InMemoryKafkaCluster()
    for payload in payloads:
        cluster.append(payload)
    ReplayableKafkaConsumer.cluster = cluster

    # Run through offset 3 (MSFT bid + 3 AAPL sells), then stop cleanly.
    ReplayableKafkaConsumer.stop_at_offset = 4
    first_engine = MatchingEngine(
        AsyncMock(), MarketDataAnalytics(order_book_cache.redis)
    )
    await OrderConsumer(matching_engine=first_engine).start()
    assert cluster.committed_offset == 4

    # Wipe Redis completely (simulates Redis restart with no persisted volume).
    await _wipe_redis_keys(stock_ids)
    assert await order_book_cache.get_order_book(msft_id) is None
    assert await order_book_cache.get_current_delta_sequence_number(msft_id) == 0

    # Restart with a fresh engine and finish consuming offsets 4..6 (AAPL only).
    ReplayableKafkaConsumer.stop_at_offset = None
    recovered_engine = MatchingEngine(
        AsyncMock(), MarketDataAnalytics(order_book_cache.redis)
    )
    await OrderConsumer(matching_engine=recovered_engine).start()

    # MSFT had zero live orders after restart, yet its Redis snapshot, deltas,
    # and sequence watermark were restored during startup replay.
    msft_snapshot = await order_book_cache.get_order_book(msft_id)
    assert msft_snapshot is not None
    assert msft_snapshot["bids"] == [
        {"price": "200.00", "quantity": "15", "order_count": 1}
    ]
    assert await order_book_cache.get_current_delta_sequence_number(msft_id) == 1
    msft_deltas = await order_book_cache.get_deltas_since(msft_id, 0)
    assert msft_deltas is not None
    assert len(msft_deltas) == 1

    # AAPL also has contiguous delta sequence numbers from 1 through the end.
    aapl_deltas = await order_book_cache.get_deltas_since(aapl_id, 0)
    assert aapl_deltas is not None
    assert [d["sequence_number"] for d in aapl_deltas] == list(
        range(1, len(aapl_deltas) + 1)
    )


@pytest.mark.asyncio
async def test_replay_handles_rejected_and_duplicate_orders_idempotently(
    monkeypatch: pytest.MonkeyPatch,
):
    """Ignore duplicate order IDs and skip domain-rejected orders during replay."""
    stock_id = uuid4()
    base = datetime(2026, 7, 26, 12, 0, 0, tzinfo=UTC)
    first_order_id = str(uuid4())

    cluster = InMemoryKafkaCluster()
    # Offset 0: valid resting order.
    cluster.append(
        {
            "type": "order",
            "id": first_order_id,
            "stock_id": str(stock_id),
            "ticker": "AAPL",
            "side": "BUY",
            "order_type": "LIMIT",
            "price": "100.00",
            "quantity": "10",
            "gateway_received_at": base.isoformat(),
        }
    )
    # Offset 1: domain-invalid STOP order (committed via OrderRejectionError).
    cluster.append(
        {
            "type": "order",
            "id": str(uuid4()),
            "stock_id": str(stock_id),
            "ticker": "AAPL",
            "side": "BUY",
            "order_type": "STOP",
            "price": "100.00",
            "quantity": "5",
            "gateway_received_at": (base + timedelta(seconds=1)).isoformat(),
        }
    )
    # Offset 2: duplicate of offset 0.
    cluster.append(
        {
            "type": "order",
            "id": first_order_id,
            "stock_id": str(stock_id),
            "ticker": "AAPL",
            "side": "BUY",
            "order_type": "LIMIT",
            "price": "100.00",
            "quantity": "10",
            "gateway_received_at": (base + timedelta(seconds=2)).isoformat(),
        }
    )
    # Offset 3: live duplicate after restart.
    cluster.append(
        {
            "type": "order",
            "id": first_order_id,
            "stock_id": str(stock_id),
            "ticker": "AAPL",
            "side": "BUY",
            "order_type": "LIMIT",
            "price": "100.00",
            "quantity": "10",
            "gateway_received_at": (base + timedelta(seconds=3)).isoformat(),
        }
    )

    monkeypatch.setattr(
        order_consumer_module,
        "AIOKafkaConsumer",
        ReplayableKafkaConsumer,
    )
    ReplayableKafkaConsumer.cluster = cluster
    cluster.committed_offset = 3
    ReplayableKafkaConsumer.stop_at_offset = None

    producer = AsyncMock()
    engine = MatchingEngine(producer, MarketDataAnalytics(order_book_cache.redis))
    await OrderConsumer(matching_engine=engine).start()

    assert cluster.committed_offset == 4
    # Offset 3 was a duplicate order ID, so zero live publishes were emitted.
    producer.send_and_wait.assert_not_awaited()
    book = engine.order_books[stock_id]
    assert book.delta_buffer.current_sequence == 1
    assert list(book.order_id_to_order.keys()) == [UUID(first_order_id)]
    assert book.order_id_to_order[UUID(first_order_id)].quantity == Decimal(10)


@pytest.mark.asyncio
async def test_replay_stall_raises_instead_of_skipping_committed_offsets(
    monkeypatch: pytest.MonkeyPatch,
):
    """Fail fast when replay stalls instead of syncing an incomplete book."""
    stock_id = uuid4()
    cluster = InMemoryKafkaCluster()
    cluster.committed_offset = 2

    class StalledConsumer(ReplayableKafkaConsumer):
        async def getmany(self, *partitions, timeout_ms=0, max_records=None):
            del timeout_ms, max_records
            tp = partitions[0] if partitions else self._cluster.tp
            return {tp: []}

    StalledConsumer.cluster = cluster
    StalledConsumer.stop_at_offset = None
    monkeypatch.setattr(order_consumer_module, "AIOKafkaConsumer", StalledConsumer)

    engine = MatchingEngine(AsyncMock(), MarketDataAnalytics(order_book_cache.redis))
    with pytest.raises(RuntimeError, match="Startup replay stalled"):
        await OrderConsumer(matching_engine=engine).start()

    assert await order_book_cache.get_order_book(stock_id) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("beginning_offset", [2, 5, 8])
async def test_truncated_orders_log_fails_fast_on_startup_replay(
    beginning_offset: int,
    monkeypatch: pytest.MonkeyPatch,
):
    """Raise on partial, complete, or past-committed log truncation."""
    cluster = InMemoryKafkaCluster()
    cluster.committed_offset = 5

    class TruncatedConsumer(ReplayableKafkaConsumer):
        async def beginning_offsets(self, partitions):
            return dict.fromkeys(partitions, beginning_offset)

    TruncatedConsumer.cluster = cluster
    TruncatedConsumer.stop_at_offset = None
    monkeypatch.setattr(order_consumer_module, "AIOKafkaConsumer", TruncatedConsumer)

    engine = MatchingEngine(AsyncMock(), MarketDataAnalytics(order_book_cache.redis))
    with pytest.raises(RuntimeError, match="truncated orders topic"):
        await OrderConsumer(matching_engine=engine).start()


@pytest.mark.asyncio
async def test_delayed_assignment_poll_rewinds_prefetched_records(
    monkeypatch: pytest.MonkeyPatch,
):
    """Rewind records fetched while waiting for initial partition assignment."""
    stock_id = uuid4()
    base = datetime(2026, 7, 26, 12, 0, 0, tzinfo=UTC)
    cluster = InMemoryKafkaCluster()
    cluster.append(
        {
            "type": "order",
            "id": str(uuid4()),
            "stock_id": str(stock_id),
            "ticker": "AAPL",
            "side": "BUY",
            "order_type": "LIMIT",
            "price": "100.00",
            "quantity": "10",
            "gateway_received_at": base.isoformat(),
        }
    )

    class DelayedAssignmentConsumer(ReplayableKafkaConsumer):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self._assigned = False

        def assignment(self) -> set[TopicPartition]:
            return {self._cluster.tp} if self._assigned else set()

        async def getmany(self, *partitions, timeout_ms=0, max_records=None):
            self._assigned = True
            return await super().getmany(
                *partitions, timeout_ms=timeout_ms, max_records=max_records
            )

    DelayedAssignmentConsumer.cluster = cluster
    DelayedAssignmentConsumer.stop_at_offset = None
    monkeypatch.setattr(
        order_consumer_module, "AIOKafkaConsumer", DelayedAssignmentConsumer
    )

    engine = MatchingEngine(AsyncMock(), MarketDataAnalytics(order_book_cache.redis))
    await OrderConsumer(matching_engine=engine).start()

    assert cluster.committed_offset == 1
    assert stock_id in engine.order_books
    assert engine.order_books[stock_id].delta_buffer.current_sequence == 1


@pytest.mark.asyncio
async def test_offset_zero_crash_with_dirty_redis_recovers_without_duplicates(
    monkeypatch: pytest.MonkeyPatch,
):
    """Recover cleanly when crashing before committing the very first offset."""
    stock_id = uuid4()
    base = datetime(2026, 7, 26, 12, 0, 0, tzinfo=UTC)
    cluster = InMemoryKafkaCluster()
    cluster.append(
        {
            "type": "order",
            "id": str(uuid4()),
            "stock_id": str(stock_id),
            "ticker": "AAPL",
            "side": "BUY",
            "order_type": "LIMIT",
            "price": "100.00",
            "quantity": "10",
            "gateway_received_at": base.isoformat(),
        }
    )

    monkeypatch.setattr(
        order_consumer_module, "AIOKafkaConsumer", ReplayableKafkaConsumer
    )
    ReplayableKafkaConsumer.cluster = cluster
    ReplayableKafkaConsumer.stop_at_offset = None

    # First run writes snapshot & deltas to Redis, then crashes before commit.
    failing_producer = AsyncMock()
    failing_producer.send_and_wait.side_effect = RuntimeError("broker timeout")
    first_engine = MatchingEngine(
        failing_producer, MarketDataAnalytics(order_book_cache.redis)
    )
    with pytest.raises(RuntimeError, match="broker timeout"):
        await OrderConsumer(matching_engine=first_engine).start()
    assert cluster.committed_offset is None
    assert await order_book_cache.get_current_delta_sequence_number(stock_id) == 1

    # Restart redelivers offset 0; store_deltas skips seq 1 without duplicating.
    recovered_producer = AsyncMock()
    recovered_engine = MatchingEngine(
        recovered_producer, MarketDataAnalytics(order_book_cache.redis)
    )
    await OrderConsumer(matching_engine=recovered_engine).start()

    assert cluster.committed_offset == 1
    deltas = await order_book_cache.get_deltas_since(stock_id, 0)
    assert deltas is not None
    assert [d["sequence_number"] for d in deltas] == [1]


@pytest.mark.asyncio
async def test_malformed_cancel_timestamp_skipped_and_missing_uses_record_ts(
    monkeypatch: pytest.MonkeyPatch,
):
    """Skip malformed cancelled_at and fall back to Kafka timestamp when omitted."""
    stock_id = uuid4()
    order_id = str(uuid4())
    base = datetime(2026, 7, 26, 12, 0, 0, tzinfo=UTC)

    cluster = InMemoryKafkaCluster()
    cluster.append(
        {
            "type": "order",
            "id": order_id,
            "stock_id": str(stock_id),
            "ticker": "AAPL",
            "side": "BUY",
            "order_type": "LIMIT",
            "price": "100.00",
            "quantity": "10",
            "gateway_received_at": base.isoformat(),
        }
    )
    # Malformed cancelled_at should be skipped cleanly (committed, not crash).
    cluster.append(
        {
            "type": "cancel",
            "order_id": order_id,
            "stock_id": str(stock_id),
            "ticker": "AAPL",
            "cancelled_at": "not-an-iso-timestamp",
        }
    )
    # Valid cancel omitting cancelled_at derives timestamp from record.timestamp.
    cancel_record = cluster.append(
        {
            "type": "cancel",
            "order_id": order_id,
            "stock_id": str(stock_id),
            "ticker": "AAPL",
        }
    )

    monkeypatch.setattr(
        order_consumer_module, "AIOKafkaConsumer", ReplayableKafkaConsumer
    )
    ReplayableKafkaConsumer.cluster = cluster
    ReplayableKafkaConsumer.stop_at_offset = None

    engine = MatchingEngine(AsyncMock(), MarketDataAnalytics(order_book_cache.redis))
    await OrderConsumer(matching_engine=engine).start()

    assert cluster.committed_offset == 3
    deltas = await order_book_cache.get_deltas_since(stock_id, 0)
    assert deltas is not None
    assert len(deltas) == 2
    assert deltas[-1]["delta_type"] == "LEVEL_REMOVE"
    assert deltas[-1]["timestamp"] == cancel_record.timestamp / 1000
