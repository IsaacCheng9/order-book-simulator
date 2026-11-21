import asyncio
import time
from types import SimpleNamespace
from typing import Any, ClassVar, cast
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import orjson
import pytest
from aiokafka import AIOKafkaConsumer, ConsumerRecord, TopicPartition

from order_book_simulator.market_data import db_consumer as db_consumer_module
from order_book_simulator.market_data.db_consumer import MarketDataDBConsumer


class StopPolling(Exception):
    """Stop a fake Kafka consumer loop in tests."""


class FakeKafkaConsumer:
    """Provide a controllable Kafka consumer for DB consumer tests."""

    poll_results: ClassVar[list[Any]] = []
    start_errors: ClassVar[list[Exception]] = []
    last_instance: ClassVar[FakeKafkaConsumer | None] = None

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.args = args
        self.kwargs = kwargs
        self.commits: list[dict[TopicPartition, int]] = []
        self.getmany_calls: list[dict[str, Any]] = []
        self.start_attempts = 0
        self.started = False
        self.stopped = False
        FakeKafkaConsumer.last_instance = self

    async def start(self) -> None:
        """Record that the fake consumer was started or raise a scripted error."""
        self.start_attempts += 1
        if self.__class__.start_errors:
            raise self.__class__.start_errors.pop(0)
        self.started = True

    async def stop(self) -> None:
        """Record that the fake consumer was stopped."""
        self.stopped = True

    async def commit(self, offsets: dict[TopicPartition, int] | None = None) -> None:
        """Record committed offsets."""
        if offsets is None:
            offsets = {}
        self.commits.append(offsets)

    async def getmany(
        self, **kwargs: Any
    ) -> dict[TopicPartition, list[ConsumerRecord]]:
        """Return configured poll results."""
        self.getmany_calls.append(kwargs)
        if not self.__class__.poll_results:
            raise StopPolling

        result = self.__class__.poll_results.pop(0)
        if result == "sleep_empty":
            await asyncio.sleep(0.02)
            return {}
        if isinstance(result, BaseException):
            raise result
        return result


def create_market_data(stock_id: str | None = None) -> dict[str, Any]:
    """Create test market data."""
    return {
        "stock_id": stock_id or str(uuid4()),
        "ticker": "TEST",
        "bids": [{"price": "100.00", "quantity": "10", "order_count": 1}],
        "asks": [{"price": "101.00", "quantity": "5", "order_count": 1}],
        "trades": [],
    }


def make_record(
    value: bytes | None,
    offset: int = 42,
    partition: int = 0,
) -> ConsumerRecord:
    """Build a minimal ConsumerRecord for tests."""
    return ConsumerRecord(
        topic="market-data",
        partition=partition,
        offset=offset,
        timestamp=0,
        timestamp_type=0,
        key=None,
        value=value,
        checksum=None,
        serialized_key_size=-1,
        serialized_value_size=-1,
        headers=(),
    )


def use_fake_kafka(
    monkeypatch: pytest.MonkeyPatch,
    poll_results: list[Any],
    start_errors: list[Exception] | None = None,
) -> None:
    """Patch the DB consumer to use a fake Kafka consumer."""
    FakeKafkaConsumer.poll_results = poll_results
    FakeKafkaConsumer.start_errors = list(start_errors or [])
    FakeKafkaConsumer.last_instance = None
    monkeypatch.setattr(db_consumer_module, "AIOKafkaConsumer", FakeKafkaConsumer)


@pytest.fixture
def consumer() -> MarketDataDBConsumer:
    """Create a consumer with small batch size for testing."""
    return MarketDataDBConsumer(
        batch_size=3,
        batch_timeout_ms=100,
        max_flush_retries=1,
        retry_backoff_ms=0,
    )


@pytest.fixture
def mock_db(monkeypatch):
    """Patch database dependencies for flush_batch tests."""
    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)
    mock_session.begin = MagicMock(return_value=AsyncMock())
    mock_session.begin.return_value.__aenter__ = AsyncMock()
    mock_session.begin.return_value.__aexit__ = AsyncMock(return_value=None)

    mock_factory = MagicMock(return_value=mock_session)
    mock_persist_snapshot = AsyncMock()
    mock_persist_order = AsyncMock()
    mock_persist_trades = AsyncMock()

    monkeypatch.setattr(db_consumer_module, "AsyncSessionLocal", mock_factory)
    monkeypatch.setattr(
        db_consumer_module,
        "persist_market_snapshot",
        mock_persist_snapshot,
    )
    monkeypatch.setattr(db_consumer_module, "persist_order", mock_persist_order)
    monkeypatch.setattr(db_consumer_module, "persist_trades", mock_persist_trades)

    return SimpleNamespace(
        factory=mock_factory,
        session=mock_session,
        persist_snapshot=mock_persist_snapshot,
        persist_order=mock_persist_order,
        persist_trades=mock_persist_trades,
    )


@pytest.mark.asyncio
async def test_flush_batch_empty_does_nothing(consumer, mock_db):
    """Flushing an empty batch should not interact with the database."""
    await consumer._flush_batch()
    mock_db.factory.assert_not_called()


@pytest.mark.asyncio
async def test_flush_batch_persists_market_data(consumer, mock_db):
    """Flushing should persist each item in the batch."""
    stock_id = str(uuid4())
    consumer.batch = [
        create_market_data(stock_id),
        create_market_data(stock_id),
    ]

    await consumer._flush_batch()

    assert mock_db.persist_snapshot.call_count == 2
    # No trades in test data, so persist_trades should not be called.
    mock_db.persist_trades.assert_not_called()


@pytest.mark.asyncio
async def test_flush_batch_persists_trades_when_present(consumer, mock_db):
    """Flushing should persist trades when they exist."""
    stock_id = str(uuid4())
    data = create_market_data(stock_id)
    data["trades"] = [
        {
            "price": "100.50",
            "quantity": "5",
            "buyer_order_id": str(uuid4()),
            "seller_order_id": str(uuid4()),
            "timestamp": "2025-01-01T00:00:00+00:00",
        }
    ]
    consumer.batch = [data]

    await consumer._flush_batch()

    mock_db.persist_snapshot.assert_called_once()
    mock_db.persist_trades.assert_called_once()


@pytest.mark.asyncio
async def test_flush_batch_clears_batch(consumer, mock_db):
    """Flushing should clear the batch."""
    consumer.batch = [create_market_data(), create_market_data()]
    topic_partition = TopicPartition("market-data", 0)
    consumer.pending_offsets = {topic_partition: 43}

    await consumer._flush_batch()

    assert len(consumer.batch) == 0
    assert consumer.pending_offsets == {}


@pytest.mark.asyncio
async def test_flush_batch_updates_last_flush_time(consumer, mock_db):
    """Flushing should update last_flush timestamp."""
    consumer.batch = [create_market_data()]
    consumer.last_flush = 0  # Set to epoch.

    before = time.time()
    await consumer._flush_batch()
    after = time.time()

    assert consumer.last_flush >= before
    assert consumer.last_flush <= after


@pytest.mark.asyncio
async def test_flush_batch_raises_and_retains_batch_on_db_error(consumer, mock_db):
    """Flushing should keep uncommitted batches when persistence fails."""
    fake_consumer = SimpleNamespace(commit=AsyncMock())
    consumer.consumer = cast(AIOKafkaConsumer, fake_consumer)
    consumer.batch = [create_market_data()]
    topic_partition = TopicPartition("market-data", 0)
    consumer.pending_offsets = {topic_partition: 43}
    mock_db.persist_snapshot.side_effect = Exception("DB error")

    with pytest.raises(Exception, match="DB error"):
        await consumer._flush_batch()

    assert len(consumer.batch) == 1
    assert consumer.pending_offsets == {topic_partition: 43}
    fake_consumer.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_flush_batch_retries_before_success(mock_db):
    """Flushing should retry transient database errors before giving up."""
    consumer = MarketDataDBConsumer(
        batch_size=3,
        batch_timeout_ms=100,
        max_flush_retries=2,
        retry_backoff_ms=0,
    )
    consumer.batch = [create_market_data()]
    mock_db.persist_snapshot.side_effect = [Exception("transient"), None]

    await consumer._flush_batch()

    assert mock_db.persist_snapshot.call_count == 2
    assert len(consumer.batch) == 0


@pytest.mark.asyncio
async def test_flush_batch_commits_offsets_after_success(consumer, mock_db):
    """Flushing should commit Kafka offsets after successful persistence."""
    fake_consumer = SimpleNamespace(commit=AsyncMock())
    consumer.consumer = cast(AIOKafkaConsumer, fake_consumer)
    consumer.batch = [create_market_data()]
    topic_partition = TopicPartition("market-data", 0)
    consumer.pending_offsets = {topic_partition: 43}

    await consumer._flush_batch()

    fake_consumer.commit.assert_awaited_once_with({topic_partition: 43})


@pytest.mark.asyncio
async def test_flush_batch_does_not_commit_offsets_on_db_error(consumer, mock_db):
    """Flushing should not commit Kafka offsets when persistence fails."""
    fake_consumer = SimpleNamespace(commit=AsyncMock())
    consumer.consumer = cast(AIOKafkaConsumer, fake_consumer)
    consumer.batch = [create_market_data()]
    consumer.pending_offsets = {TopicPartition("market-data", 0): 43}
    mock_db.persist_snapshot.side_effect = Exception("DB error")

    with pytest.raises(Exception, match="DB error"):
        await consumer._flush_batch()

    fake_consumer.commit.assert_not_awaited()


def test_batch_size_threshold(consumer):
    """Consumer should identify when batch size threshold is reached."""
    consumer.batch = [create_market_data() for _ in range(2)]
    consumer.last_flush = time.time()

    assert not consumer._should_flush()

    consumer.batch.append(create_market_data())
    assert consumer._should_flush()


def test_batch_timeout_threshold(consumer):
    """Consumer should identify when batch timeout is reached."""
    consumer.batch = [create_market_data()]
    consumer.last_flush = time.time()

    assert not consumer._should_flush()

    consumer.last_flush = time.time() - consumer.batch_timeout - 0.01
    assert consumer._should_flush()


def test_add_to_batch_tracks_highest_offsets(consumer):
    """Consumer should track the highest pending offset for each partition."""
    consumer._add_to_batch(create_market_data(), make_record(b"{}", offset=3))
    consumer._add_to_batch(create_market_data(), make_record(b"{}", offset=7))

    assert consumer.pending_offsets == {TopicPartition("market-data", 0): 8}


@pytest.mark.asyncio
async def test_start_disables_auto_commit(
    consumer,
    monkeypatch: pytest.MonkeyPatch,
):
    """Consumer should disable Kafka auto-commit."""
    use_fake_kafka(monkeypatch, [StopPolling()])

    with pytest.raises(StopPolling):
        await consumer.start()

    fake_consumer = FakeKafkaConsumer.last_instance
    assert fake_consumer is not None
    assert fake_consumer.kwargs["enable_auto_commit"] is False
    assert fake_consumer.kwargs["auto_offset_reset"] == "earliest"
    assert fake_consumer.stopped is True


@pytest.mark.asyncio
async def test_start_flushes_partial_batch_after_timeout(
    mock_db,
    monkeypatch: pytest.MonkeyPatch,
):
    """Consumer should flush partial batches even when the topic goes quiet."""
    consumer = MarketDataDBConsumer(
        batch_size=3,
        batch_timeout_ms=10,
        max_flush_retries=1,
        retry_backoff_ms=0,
    )
    topic_partition = TopicPartition("market-data", 0)
    payload = orjson.dumps(create_market_data())
    use_fake_kafka(
        monkeypatch,
        [
            {topic_partition: [make_record(payload, offset=0)]},
            "sleep_empty",
            StopPolling(),
        ],
    )

    with pytest.raises(StopPolling):
        await consumer.start()

    fake_consumer = FakeKafkaConsumer.last_instance
    assert fake_consumer is not None
    assert mock_db.persist_snapshot.call_count == 1
    assert fake_consumer.commits == [{topic_partition: 1}]
    assert consumer.batch == []


@pytest.mark.asyncio
async def test_start_commits_empty_messages_without_persisting(
    mock_db,
    monkeypatch: pytest.MonkeyPatch,
):
    """Consumer should commit empty Kafka records without adding them to a batch."""
    consumer = MarketDataDBConsumer(max_flush_retries=1, retry_backoff_ms=0)
    topic_partition = TopicPartition("market-data", 0)
    use_fake_kafka(
        monkeypatch,
        [
            {topic_partition: [make_record(None, offset=5)]},
            StopPolling(),
        ],
    )

    with pytest.raises(StopPolling):
        await consumer.start()

    fake_consumer = FakeKafkaConsumer.last_instance
    assert fake_consumer is not None
    assert fake_consumer.commits == [{topic_partition: 6}]
    mock_db.persist_snapshot.assert_not_called()


@pytest.mark.asyncio
async def test_start_commits_malformed_messages_without_persisting(
    mock_db,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
):
    """Consumer should skip and commit malformed Kafka records."""
    caplog.set_level("ERROR")
    consumer = MarketDataDBConsumer(max_flush_retries=1, retry_backoff_ms=0)
    topic_partition = TopicPartition("market-data", 0)
    use_fake_kafka(
        monkeypatch,
        [
            {topic_partition: [make_record(b"not json", offset=8)]},
            StopPolling(),
        ],
    )

    with pytest.raises(StopPolling):
        await consumer.start()

    fake_consumer = FakeKafkaConsumer.last_instance
    assert fake_consumer is not None
    assert fake_consumer.commits == [{topic_partition: 9}]
    assert "Skipping malformed market data Kafka message" in caplog.text
    mock_db.persist_snapshot.assert_not_called()


@pytest.mark.parametrize(
    "skipped_value",
    [None, b"not json"],
    ids=["empty", "malformed"],
)
@pytest.mark.asyncio
async def test_start_defers_skipped_offset_until_buffered_record_is_persisted(
    skipped_value: bytes | None,
    mock_db,
    monkeypatch: pytest.MonkeyPatch,
):
    """Commit a skipped record only after earlier buffered data is durable."""
    consumer = MarketDataDBConsumer(
        batch_size=50,
        batch_timeout_ms=60_000,
        max_flush_retries=1,
        retry_backoff_ms=0,
    )
    topic_partition = TopicPartition("market-data", 0)
    valid_payload = orjson.dumps(create_market_data())
    use_fake_kafka(
        monkeypatch,
        [
            {
                topic_partition: [
                    make_record(valid_payload, offset=5),
                    make_record(skipped_value, offset=6),
                ]
            },
            StopPolling(),
        ],
    )

    with pytest.raises(StopPolling):
        await consumer.start()

    fake_consumer = FakeKafkaConsumer.last_instance
    assert fake_consumer is not None
    mock_db.persist_snapshot.assert_awaited_once()
    assert fake_consumer.commits == [{topic_partition: 7}]


@pytest.mark.asyncio
async def test_start_retains_skipped_offset_when_earlier_persistence_fails(
    mock_db,
    monkeypatch: pytest.MonkeyPatch,
):
    """Leave skipped and buffered records uncommitted after a database failure."""
    consumer = MarketDataDBConsumer(
        batch_size=50,
        batch_timeout_ms=60_000,
        max_flush_retries=1,
        retry_backoff_ms=0,
    )
    topic_partition = TopicPartition("market-data", 0)
    valid_payload = orjson.dumps(create_market_data())
    mock_db.persist_snapshot.side_effect = RuntimeError("database unavailable")
    use_fake_kafka(
        monkeypatch,
        [
            {
                topic_partition: [
                    make_record(valid_payload, offset=5),
                    make_record(b"not json", offset=6),
                ]
            },
            StopPolling(),
        ],
    )

    with pytest.raises(RuntimeError, match="database unavailable"):
        await consumer.start()

    fake_consumer = FakeKafkaConsumer.last_instance
    assert fake_consumer is not None
    assert fake_consumer.commits == []
    assert fake_consumer.stopped is True
    assert consumer.pending_offsets == {topic_partition: 7}


@pytest.mark.asyncio
async def test_start_retries_kafka_connection_before_consuming(
    monkeypatch: pytest.MonkeyPatch,
):
    """Retry transient Kafka startup errors before giving up."""
    consumer = MarketDataDBConsumer(max_retries=3, retry_delay=0)
    use_fake_kafka(
        monkeypatch,
        [StopPolling()],
        start_errors=[RuntimeError("broker warming up")],
    )

    with pytest.raises(StopPolling):
        await consumer.start()

    fake_consumer = FakeKafkaConsumer.last_instance
    assert fake_consumer is not None
    assert fake_consumer.start_attempts == 2
    assert fake_consumer.started is True
    assert fake_consumer.stopped is True


@pytest.mark.asyncio
async def test_start_stops_consumer_when_connection_retries_exhausted(
    monkeypatch: pytest.MonkeyPatch,
):
    """Stop the Kafka client if all startup connection retries fail."""
    consumer = MarketDataDBConsumer(max_retries=2, retry_delay=0)
    use_fake_kafka(
        monkeypatch,
        [],
        start_errors=[RuntimeError("down 1"), RuntimeError("down 2")],
    )

    with pytest.raises(RuntimeError, match="down 2"):
        await consumer.start()

    fake_consumer = FakeKafkaConsumer.last_instance
    assert fake_consumer is not None
    assert fake_consumer.start_attempts == 2
    assert fake_consumer.stopped is True
    assert consumer.consumer is None


@pytest.mark.asyncio
async def test_start_does_not_double_flush_on_mid_loop_failure(
    mock_db,
    monkeypatch: pytest.MonkeyPatch,
):
    """Avoid retrying a failed flush in finally and still stop the consumer."""
    consumer = MarketDataDBConsumer(
        batch_size=1,
        batch_timeout_ms=60_000,
        max_flush_retries=2,
        retry_backoff_ms=0,
    )
    topic_partition = TopicPartition("market-data", 0)
    valid_payload = orjson.dumps(create_market_data())
    mock_db.persist_snapshot.side_effect = RuntimeError("overflow")
    use_fake_kafka(
        monkeypatch,
        [{topic_partition: [make_record(valid_payload, offset=10)]}],
    )

    with pytest.raises(RuntimeError, match="overflow"):
        await consumer.start()

    fake_consumer = FakeKafkaConsumer.last_instance
    assert fake_consumer is not None
    # Only the mid-loop flush ran (2 attempts), not a second flush in finally.
    assert mock_db.persist_snapshot.await_count == 2
    assert fake_consumer.commits == []
    assert fake_consumer.stopped is True
    assert consumer.consumer is None


@pytest.mark.asyncio
async def test_flush_batch_persists_order_updates_and_passes_known_ids(
    consumer, mock_db
):
    """Upsert order_updates first and pass their IDs to persist_trades."""
    stock_id = str(uuid4())
    buyer_id = str(uuid4())
    seller_id = str(uuid4())
    now = "2026-07-26T12:00:00+00:00"
    data = create_market_data(stock_id)
    data["order_updates"] = [
        {
            "id": buyer_id,
            "user_id": str(uuid4()),
            "stock_id": stock_id,
            "type": "LIMIT",
            "side": "BUY",
            "status": "FILLED",
            "price": "100.50",
            "quantity": "5",
            "filled_quantity": "5",
            "total_fee": "0",
            "time_in_force": "GTC",
            "client_order_id": "cid-buy",
            "created_at": now,
            "updated_at": now,
        },
        {
            "id": seller_id,
            "user_id": str(uuid4()),
            "stock_id": stock_id,
            "type": "LIMIT",
            "side": "SELL",
            "status": "FILLED",
            "price": "100.50",
            "quantity": "5",
            "filled_quantity": "5",
            "total_fee": "0",
            "time_in_force": "GTC",
            "client_order_id": "cid-sell",
            "created_at": now,
            "updated_at": now,
        },
    ]
    data["trades"] = [
        {
            "id": str(uuid4()),
            "price": "100.50",
            "quantity": "5",
            "buyer_order_id": buyer_id,
            "seller_order_id": seller_id,
            "timestamp": now,
        }
    ]
    consumer.batch = [data]

    await consumer._flush_batch()

    assert mock_db.persist_order.await_count == 2
    mock_db.persist_snapshot.assert_awaited_once()
    mock_db.persist_trades.assert_awaited_once()
    passed_known = mock_db.persist_trades.await_args.kwargs["known_order_ids"]
    assert {str(oid) for oid in passed_known} == {buyer_id, seller_id}


@pytest.mark.parametrize(
    "bad_payload",
    [
        {
            "schema_version": 2,
            "stock_id": str(uuid4()),
            "ticker": "AAPL",
            "bids": [],
            "asks": [],
        },
        {"stock_id": "not-a-uuid", "ticker": "AAPL", "bids": [], "asks": []},
        {
            "stock_id": str(uuid4()),
            "ticker": "AAPL",
            "bids": [{"price": "1E+30", "quantity": "1"}],
            "asks": [],
        },
        {
            "stock_id": str(uuid4()),
            "ticker": "AAPL",
            "bids": [],
            "asks": [],
            "order_updates": [
                {
                    "id": str(uuid4()),
                    "user_id": str(uuid4()),
                    "stock_id": str(uuid4()),
                    "type": "LIMIT",
                    "side": "BUY",
                    "status": "FILLED",
                    "price": "100",
                    "quantity": "10",
                    "filled_quantity": "5",
                    "created_at": "2026-07-26T12:00:00+00:00",
                    "updated_at": "2026-07-26T12:00:00+00:00",
                }
            ],
        },
    ],
)
@pytest.mark.asyncio
async def test_semantic_invalid_market_data_skipped_without_blocking_neighbours(
    bad_payload: dict[str, Any],
    mock_db,
    monkeypatch: pytest.MonkeyPatch,
):
    """Reject semantically invalid events before batching while preserving valid ones."""
    consumer = MarketDataDBConsumer(
        batch_size=50,
        batch_timeout_ms=60_000,
        max_flush_retries=1,
        retry_backoff_ms=0,
    )
    tp = TopicPartition("market-data", 0)
    valid_before = orjson.dumps(create_market_data())
    invalid_middle = orjson.dumps(bad_payload)
    valid_after = orjson.dumps(create_market_data())

    use_fake_kafka(
        monkeypatch,
        [
            {
                tp: [
                    make_record(valid_before, offset=10),
                    make_record(invalid_middle, offset=11),
                    make_record(valid_after, offset=12),
                ]
            },
            StopPolling(),
        ],
    )

    with pytest.raises(StopPolling):
        await consumer.start()

    fake_consumer = FakeKafkaConsumer.last_instance
    assert fake_consumer is not None
    assert mock_db.persist_snapshot.await_count == 2
    assert fake_consumer.commits == [{tp: 13}]


@pytest.mark.asyncio
async def test_semantic_invalid_after_buffered_valid_stays_uncommitted_on_db_error(
    mock_db,
    monkeypatch: pytest.MonkeyPatch,
):
    """Never commit a semantically invalid offset ahead of a failed earlier batch."""
    consumer = MarketDataDBConsumer(
        batch_size=50,
        batch_timeout_ms=60_000,
        max_flush_retries=1,
        retry_backoff_ms=0,
    )
    tp = TopicPartition("market-data", 0)
    valid_payload = orjson.dumps(create_market_data())
    bad_payload = orjson.dumps(
        {
            "schema_version": 99,
            "stock_id": str(uuid4()),
            "ticker": "AAPL",
            "bids": [],
            "asks": [],
        }
    )
    mock_db.persist_snapshot.side_effect = RuntimeError("transient db disconnect")

    use_fake_kafka(
        monkeypatch,
        [
            {
                tp: [
                    make_record(valid_payload, offset=20),
                    make_record(bad_payload, offset=21),
                ]
            },
            StopPolling(),
        ],
    )

    with pytest.raises(RuntimeError, match="transient db disconnect"):
        await consumer.start()

    fake_consumer = FakeKafkaConsumer.last_instance
    assert fake_consumer is not None
    assert fake_consumer.commits == []
    assert fake_consumer.stopped is True
