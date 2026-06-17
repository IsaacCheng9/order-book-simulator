from datetime import datetime, timezone
from typing import Any, ClassVar, cast
from unittest.mock import AsyncMock
from uuid import uuid4

import orjson
import pytest
from aiokafka import ConsumerRecord

from order_book_simulator.matching import order_consumer as order_consumer_module
from order_book_simulator.matching.matching_engine import MatchingEngine
from order_book_simulator.matching.order_consumer import OrderConsumer


class FakeKafkaConsumer:
    """Provide an async iterable Kafka consumer for start-loop tests."""

    queued_messages: ClassVar[list[ConsumerRecord]] = []
    last_instance: ClassVar["FakeKafkaConsumer | None"] = None

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.messages = list(self.__class__.queued_messages)
        self.commit_count = 0
        self.started = False
        self.stopped = False
        FakeKafkaConsumer.last_instance = self

    async def start(self) -> None:
        """Record that the fake consumer was started."""
        self.started = True

    async def stop(self) -> None:
        """Record that the fake consumer was stopped."""
        self.stopped = True

    async def commit(self) -> None:
        """Record an offset commit."""
        self.commit_count += 1

    def __aiter__(self) -> "FakeKafkaConsumer":
        return self

    async def __anext__(self) -> ConsumerRecord:
        if not self.messages:
            raise StopAsyncIteration
        return self.messages.pop(0)


def make_record(value: bytes | None, key: bytes | None = None) -> ConsumerRecord:
    """Builds a minimal ConsumerRecord for tests."""
    return ConsumerRecord(
        topic="orders",
        partition=0,
        offset=42,
        timestamp=0,
        timestamp_type=0,
        key=key,
        value=value,
        checksum=None,
        serialized_key_size=-1,
        serialized_value_size=-1,
        headers=(),
    )


@pytest.fixture
def engine() -> AsyncMock:
    """Creates an AsyncMock standing in for the matching engine."""
    return AsyncMock()


@pytest.fixture
def consumer(engine: AsyncMock) -> OrderConsumer:
    """Creates an OrderConsumer wired to the mocked matching engine."""
    return OrderConsumer(matching_engine=cast(MatchingEngine, engine))


def valid_order_payload() -> bytes:
    """Build a well-formed order payload for consumer tests."""
    return orjson.dumps(
        {
            "type": "order",
            "id": str(uuid4()),
            "stock_id": str(uuid4()),
            "ticker": "AAPL",
            "side": "BUY",
            "order_type": "LIMIT",
            "price": "100",
            "quantity": "10",
            "gateway_received_at": datetime.now(timezone.utc).isoformat(),
        }
    )


@pytest.mark.asyncio
async def test_process_order_message_calls_matching_engine(
    consumer: OrderConsumer, engine: AsyncMock
):
    """Tests that a well-formed order is forwarded to the matching engine."""
    order_id = str(uuid4())
    payload = orjson.dumps(
        {
            "type": "order",
            "id": order_id,
            "stock_id": str(uuid4()),
            "ticker": "AAPL",
            "side": "BUY",
            "order_type": "LIMIT",
            "price": "100",
            "quantity": "10",
            "gateway_received_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    await consumer._process_message(make_record(payload))

    engine.process_order.assert_awaited_once()
    forwarded = engine.process_order.await_args.args[0]
    assert forwarded["id"] == order_id


@pytest.mark.asyncio
async def test_process_cancel_message_calls_cancel(
    consumer: OrderConsumer, engine: AsyncMock
):
    """Tests that a cancel message is routed to cancel_order."""
    payload = orjson.dumps(
        {
            "type": "cancel",
            "order_id": str(uuid4()),
            "stock_id": str(uuid4()),
            "ticker": "AAPL",
        }
    )
    await consumer._process_message(make_record(payload))

    engine.cancel_order.assert_awaited_once()


@pytest.mark.asyncio
async def test_health_check_is_skipped(consumer: OrderConsumer, engine: AsyncMock):
    """Tests that health check messages are silently ignored."""
    payload = orjson.dumps({"type": "health_check"})
    await consumer._process_message(make_record(payload))

    engine.process_order.assert_not_awaited()
    engine.cancel_order.assert_not_awaited()


@pytest.mark.asyncio
async def test_malformed_json_is_skipped_with_context(
    consumer: OrderConsumer,
    engine: AsyncMock,
    caplog: pytest.LogCaptureFixture,
):
    """Tests that invalid JSON is logged with full context and does not raise."""
    caplog.set_level("ERROR")
    record = make_record(b"not valid json{{{", key=b"some-key")

    # Should not raise - this is a recoverable error.
    await consumer._process_message(record)

    engine.process_order.assert_not_awaited()
    assert "Skipping malformed Kafka message" in caplog.text
    assert "topic=orders" in caplog.text
    assert "partition=0" in caplog.text
    assert "offset=42" in caplog.text
    assert "key='some-key'" in caplog.text
    assert "JSONDecodeError" in caplog.text


@pytest.mark.asyncio
async def test_unknown_order_type_is_skipped(
    consumer: OrderConsumer,
    engine: AsyncMock,
    caplog: pytest.LogCaptureFixture,
):
    """Tests that an unrecognised message type is logged and skipped."""
    caplog.set_level("ERROR")
    payload = orjson.dumps({"type": "definitely-not-real"})

    await consumer._process_message(make_record(payload))

    assert "Unknown message type" in caplog.text
    assert "ValueError" in caplog.text
    engine.process_order.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_required_field_is_skipped_with_order_id(
    consumer: OrderConsumer, caplog: pytest.LogCaptureFixture
):
    """Tests that a missing field after parsing the order ID logs the ID."""
    caplog.set_level("ERROR")
    order_id = str(uuid4())
    # Missing gateway_received_at - KeyError after order_id is set.
    payload = orjson.dumps({"type": "order", "id": order_id})

    await consumer._process_message(make_record(payload))

    assert "KeyError" in caplog.text
    assert f"order_id='{order_id}'" in caplog.text


@pytest.mark.asyncio
async def test_unexpected_exception_propagates(
    consumer: OrderConsumer, engine: AsyncMock
):
    """
    Tests that a non-recoverable exception (e.g. matching engine crash)
    propagates out of _process_message rather than being swallowed.
    """
    engine.process_order.side_effect = RuntimeError("matching engine exploded")
    payload = orjson.dumps(
        {
            "type": "order",
            "id": str(uuid4()),
            "stock_id": str(uuid4()),
            "ticker": "AAPL",
            "side": "BUY",
            "order_type": "LIMIT",
            "price": "100",
            "quantity": "10",
            "gateway_received_at": datetime.now(timezone.utc).isoformat(),
        }
    )

    with pytest.raises(RuntimeError, match="matching engine exploded"):
        await consumer._process_message(make_record(payload))


@pytest.mark.asyncio
async def test_matching_engine_value_error_propagates(
    consumer: OrderConsumer,
    engine: AsyncMock,
):
    """Test that engine ValueErrors are not treated as malformed messages."""
    engine.process_order.side_effect = ValueError("engine invariant failed")

    with pytest.raises(ValueError, match="engine invariant failed"):
        await consumer._process_message(make_record(valid_order_payload()))


@pytest.mark.asyncio
async def test_matching_engine_type_error_propagates(
    consumer: OrderConsumer,
    engine: AsyncMock,
):
    """Test that engine TypeErrors are not treated as malformed messages."""
    engine.process_order.side_effect = TypeError("engine type bug")

    with pytest.raises(TypeError, match="engine type bug"):
        await consumer._process_message(make_record(valid_order_payload()))


@pytest.mark.asyncio
async def test_invalid_order_schema_is_skipped_before_engine(
    consumer: OrderConsumer,
    engine: AsyncMock,
    caplog: pytest.LogCaptureFixture,
):
    """Test that malformed order fields are skipped before engine handoff."""
    caplog.set_level("ERROR")
    payload = orjson.dumps(
        {
            "type": "order",
            "id": str(uuid4()),
            "stock_id": "not-a-uuid",
            "ticker": "AAPL",
            "side": "BUY",
            "order_type": "LIMIT",
            "price": "100",
            "quantity": "10",
            "gateway_received_at": datetime.now(timezone.utc).isoformat(),
        }
    )

    await consumer._process_message(make_record(payload))

    assert "stock_id must be a UUID" in caplog.text
    engine.process_order.assert_not_awaited()


@pytest.mark.asyncio
async def test_truncates_oversized_payload_in_log(
    consumer: OrderConsumer, caplog: pytest.LogCaptureFixture
):
    """Tests that an oversized malformed payload is truncated in the log."""
    caplog.set_level("ERROR")
    # 2 KB of junk - should be truncated to 500 chars in the log.
    payload = b"x" * 2048

    await consumer._process_message(make_record(payload))

    # The truncated payload representation appears, not the full 2 KB.
    assert "payload='" in caplog.text
    assert "x" * 2049 not in caplog.text


@pytest.mark.asyncio
async def test_start_commits_after_skipping_malformed_message(
    consumer: OrderConsumer,
    monkeypatch: pytest.MonkeyPatch,
):
    """Test that deliberately skipped malformed messages still commit."""
    FakeKafkaConsumer.queued_messages = [make_record(b"not valid json{{{")]
    FakeKafkaConsumer.last_instance = None
    monkeypatch.setattr(
        order_consumer_module,
        "AIOKafkaConsumer",
        FakeKafkaConsumer,
    )

    await consumer.start()

    fake_consumer = FakeKafkaConsumer.last_instance
    assert fake_consumer is not None
    assert fake_consumer.commit_count == 1
    assert fake_consumer.stopped is True


@pytest.mark.asyncio
async def test_start_does_not_commit_when_engine_error_propagates(
    consumer: OrderConsumer,
    engine: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
):
    """Test that engine failures leave the Kafka offset uncommitted."""
    engine.process_order.side_effect = ValueError("engine invariant failed")
    FakeKafkaConsumer.queued_messages = [make_record(valid_order_payload())]
    FakeKafkaConsumer.last_instance = None
    monkeypatch.setattr(
        order_consumer_module,
        "AIOKafkaConsumer",
        FakeKafkaConsumer,
    )

    with pytest.raises(ValueError, match="engine invariant failed"):
        await consumer.start()

    fake_consumer = FakeKafkaConsumer.last_instance
    assert fake_consumer is not None
    assert fake_consumer.commit_count == 0
    assert fake_consumer.stopped is True
