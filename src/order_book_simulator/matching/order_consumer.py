import asyncio
import logging
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

import orjson
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer, ConsumerRecord
from redis.asyncio import Redis

from order_book_simulator.common.cache import order_book_cache
from order_book_simulator.common.events import (
    MAX_CLIENT_ORDER_ID_LENGTH,
    MAX_TICKER_LENGTH,
    MAX_TIME_IN_FORCE_LENGTH,
    validate_schema_version,
)
from order_book_simulator.common.models import OrderSide, OrderType
from order_book_simulator.market_data.analytics import MarketDataAnalytics
from order_book_simulator.matching.matching_engine import MatchingEngine
from order_book_simulator.matching.order_book import OrderRejectionError
from order_book_simulator.multicast.multicast_publisher import MulticastPublisher

logger = logging.getLogger(__name__)
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)

# Exceptions that indicate the Kafka message cannot be parsed or does
# not match the consumer envelope contract. These are skipped with
# structured context because retrying the same bytes will not help.
MALFORMED_MESSAGE_ERRORS = (
    orjson.JSONDecodeError,
    InvalidOperation,
    KeyError,
    ValueError,
    TypeError,
)


class OrderConsumer:
    """Processes incoming orders from Kafka and routes them to the matching engine."""

    def __init__(
        self,
        matching_engine: MatchingEngine,
        bootstrap_servers: str | None = None,
        topic: str = "orders",
        group_id: str = "matching-engine",
        max_retries: int = 5,
        retry_delay: int = 5,
        session_timeout_ms: int = 10000,
        heartbeat_interval_ms: int = 1000,
    ):
        """
        Creates a new order consumer.

        Args:
            matching_engine: The matching engine instance to process orders.
            bootstrap_servers: Kafka bootstrap servers string. Defaults to
                ``KAFKA_BOOTSTRAP_SERVERS`` or ``kafka:9092``.
            topic: Kafka topic to consume from.
            group_id: Consumer group ID.
            max_retries: Maximum number of connection attempts.
            retry_delay: Delay between connection attempts in seconds.
            session_timeout_ms: Kafka session timeout in milliseconds.
            heartbeat_interval_ms: Kafka heartbeat interval in milliseconds.
        """
        self.consumer: AIOKafkaConsumer | None = None
        self.matching_engine = matching_engine
        self.bootstrap_servers = bootstrap_servers or os.getenv(
            "KAFKA_BOOTSTRAP_SERVERS", "kafka:9092"
        )
        self.topic = topic
        self.group_id = group_id
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.session_timeout_ms = session_timeout_ms
        self.heartbeat_interval_ms = heartbeat_interval_ms

    @staticmethod
    def _decode_message_payload(value: bytes) -> dict[str, Any]:
        """Decode a Kafka payload into a JSON object.

        Args:
            value: The raw Kafka message value.

        Returns:
            The decoded JSON object.

        Raises:
            TypeError: If the payload is not a JSON object.
        """
        order_data = orjson.loads(value)
        if not isinstance(order_data, dict):
            raise TypeError("Kafka message payload must be a JSON object")
        return order_data

    @staticmethod
    def _require_string(order_data: dict[str, Any], field: str) -> str:
        """Return a required non-empty string field from a Kafka message.

        Args:
            order_data: The decoded Kafka message.
            field: The field name to read.

        Returns:
            The validated string value.

        Raises:
            KeyError: If the field is missing.
            TypeError: If the field is not a non-empty string.
        """
        value = order_data[field]
        if not isinstance(value, str) or not value:
            raise TypeError(f"{field} must be a non-empty string")
        if "\x00" in value:
            raise ValueError(f"{field} must not contain NUL bytes")
        return value

    @staticmethod
    def _validate_uuid(value: str, field: str) -> None:
        """Validate that a string field contains a UUID.

        Args:
            value: The candidate UUID string.
            field: The source field name used in error messages.

        Raises:
            ValueError: If the value is not a UUID.
        """
        try:
            UUID(value)
        except ValueError as exc:
            raise ValueError(f"{field} must be a UUID") from exc

    @staticmethod
    def _validate_decimal(value: Any, field: str) -> None:
        """Validate that a field can be parsed as a decimal value.

        Args:
            value: The candidate decimal value.
            field: The source field name used in error messages.

        Raises:
            TypeError: If the value is null.
            InvalidOperation: If the value cannot be parsed as a Decimal.
        """
        if value is None:
            raise TypeError(f"{field} must not be null")
        Decimal(str(value))

    @staticmethod
    def _parse_gateway_time(order_data: dict[str, Any]) -> datetime:
        """Parse the gateway timestamp from an order message.

        Args:
            order_data: The decoded order message.

        Returns:
            The parsed gateway timestamp.

        Raises:
            ValueError: If the timestamp is invalid or timezone-naive.
        """
        raw_gateway_time = OrderConsumer._require_string(
            order_data, "gateway_received_at"
        )
        gateway_time = datetime.fromisoformat(raw_gateway_time)
        if gateway_time.tzinfo is None:
            raise ValueError("gateway_received_at must include a timezone")
        return gateway_time

    @staticmethod
    def _require_ticker(order_data: dict[str, Any]) -> str:
        """Return a required ticker string bounded by stock.ticker VARCHAR(20)."""
        ticker = OrderConsumer._require_string(order_data, "ticker")
        if len(ticker) > MAX_TICKER_LENGTH:
            raise ValueError(f"ticker exceeds maximum length of {MAX_TICKER_LENGTH}")
        return ticker

    def _validate_order_message(self, order_data: dict[str, Any]) -> datetime:
        """Validate an order message before handing it to the engine.

        This only validates the Kafka envelope and parseable field types.
        Matching-domain validation belongs to the matching engine so those
        failures propagate instead of being committed as skipped messages.

        Args:
            order_data: The decoded order message.

        Returns:
            The parsed gateway timestamp used for latency logging.
        """
        validate_schema_version(order_data)
        self._validate_uuid(self._require_string(order_data, "id"), "id")
        self._validate_uuid(self._require_string(order_data, "stock_id"), "stock_id")
        self._require_ticker(order_data)
        raw_user_id = order_data.get("user_id")
        if raw_user_id is not None:
            if not isinstance(raw_user_id, str) or not raw_user_id:
                raise TypeError("user_id must be a non-empty UUID string")
            self._validate_uuid(raw_user_id, "user_id")
        raw_tif = order_data.get("time_in_force")
        if raw_tif is not None:
            if not isinstance(raw_tif, str):
                raise TypeError("time_in_force must be a string or null")
            if "\x00" in raw_tif:
                raise ValueError("time_in_force must not contain NUL bytes")
            if len(raw_tif) > MAX_TIME_IN_FORCE_LENGTH:
                raise ValueError(
                    f"time_in_force exceeds maximum length of "
                    f"{MAX_TIME_IN_FORCE_LENGTH}"
                )
        raw_client_order_id = order_data.get("client_order_id")
        if raw_client_order_id is not None:
            if not isinstance(raw_client_order_id, str):
                raise TypeError("client_order_id must be a string or null")
            if "\x00" in raw_client_order_id:
                raise ValueError("client_order_id must not contain NUL bytes")
            if len(raw_client_order_id) > MAX_CLIENT_ORDER_ID_LENGTH:
                raise ValueError(
                    f"client_order_id exceeds maximum length of "
                    f"{MAX_CLIENT_ORDER_ID_LENGTH}"
                )
        OrderSide(self._require_string(order_data, "side"))
        OrderType(self._require_string(order_data, "order_type"))
        self._validate_decimal(order_data["quantity"], "quantity")
        if "price" not in order_data:
            raise KeyError("price")
        if order_data["price"] is not None:
            self._validate_decimal(order_data["price"], "price")
        return self._parse_gateway_time(order_data)

    def _validate_cancel_message(self, order_data: dict[str, Any]) -> None:
        """Validate a cancellation message before handing it to the engine.

        Args:
            order_data: The decoded cancellation message.
        """
        validate_schema_version(order_data)
        self._validate_uuid(self._require_string(order_data, "order_id"), "order_id")
        self._validate_uuid(self._require_string(order_data, "stock_id"), "stock_id")
        self._require_ticker(order_data)
        raw_cancelled_at = order_data.get("cancelled_at")
        if raw_cancelled_at is not None:
            if not isinstance(raw_cancelled_at, str) or not raw_cancelled_at:
                raise TypeError("cancelled_at must be a non-empty ISO timestamp string")
            parsed = datetime.fromisoformat(raw_cancelled_at)
            if parsed.tzinfo is None:
                raise ValueError("cancelled_at must include a timezone")

    async def _process_message(
        self,
        message: ConsumerRecord,
        *,
        publish: bool = True,
    ) -> None:
        """Process a single message from Kafka.

        Malformed or unknown messages are logged with full context and
        skipped. Unexpected errors propagate so the consumer crashes
        rather than processing bad state silently.

        Args:
            message: The message received from Kafka.
            publish: Whether downstream side effects should be emitted.
                Set to False during startup replay up to committed offsets.
        """
        order_id: str | None = None
        message_type: str | None = None
        order_data: dict[str, Any] = {}
        gateway_time: datetime | None = None
        try:
            if message.value is None:
                return
            order_data = self._decode_message_payload(message.value)

            message_type = self._require_string(order_data, "type")
            match message_type:
                case "health_check":
                    return
                case "cancel":
                    order_id = self._require_string(order_data, "order_id")
                    self._validate_cancel_message(order_data)
                case "order":
                    order_id = self._require_string(order_data, "id")
                    gateway_time = self._validate_order_message(order_data)
                case _:
                    raise ValueError(f"Unknown message type: {message_type!r}")
        except MALFORMED_MESSAGE_ERRORS as e:
            self._log_skipped_message(message, e, order_id)
            return

        if message_type == "cancel":
            # Ensure a deterministic cancel timestamp from the Kafka record
            # when the producer omitted cancelled_at.
            if (
                order_data.get("cancelled_at") is None
                and message.timestamp is not None
                and message.timestamp >= 0
            ):
                order_data["cancelled_at"] = (
                    EPOCH + timedelta(milliseconds=message.timestamp)
                ).isoformat()
            if publish:
                logger.info(f"Processing cancellation: {order_id=}")
                result = await self.matching_engine.cancel_order(order_data)
                logger.info(f"Cancelled order {order_id=}: {result}")
            else:
                await self.matching_engine.cancel_order(order_data, publish=False)
            return

        if message_type == "order":
            if gateway_time is None:
                raise RuntimeError("Order message was parsed without gateway time.")
            if not publish:
                try:
                    await self.matching_engine.process_order(order_data, publish=False)
                except OrderRejectionError as exc:
                    self._log_rejected_order(message, exc, order_id)
                return

            # Track the latency for live orders.
            kafka_latency = (datetime.now(UTC) - gateway_time).total_seconds() * 1000
            logger.info(
                f"Processing order: {order_id=}, kafka_latency={kafka_latency:.2f}ms"
            )

            start_process = datetime.now(UTC)
            try:
                await self.matching_engine.process_order(order_data)
            except OrderRejectionError as exc:
                self._log_rejected_order(message, exc, order_id)
                return
            process_time = (datetime.now(UTC) - start_process).total_seconds() * 1000
            total_latency = (datetime.now(UTC) - gateway_time).total_seconds() * 1000
            logger.info(
                f"Order processed: {order_id=}, "
                f"matching_time={process_time:.2f}ms, "
                f"total_latency={total_latency:.2f}ms"
            )

    def _log_rejected_order(
        self,
        message: ConsumerRecord,
        error: OrderRejectionError,
        order_id: str | None,
    ) -> None:
        """Log a domain-rejected order with Kafka coordinates before committing."""
        logger.warning(
            "Rejecting domain-invalid Kafka order: "
            f"error={error!s}, "
            f"topic={message.topic}, "
            f"partition={message.partition}, "
            f"offset={message.offset}, "
            f"order_id={order_id!r}"
        )

    def _log_skipped_message(
        self,
        message: ConsumerRecord,
        error: Exception,
        order_id: str | None,
    ) -> None:
        """
        Logs a malformed Kafka message with the full context needed
        to debug or replay it.

        Args:
            message: The Kafka message that failed to process.
            error: The recoverable exception raised while processing.
            order_id: The parsed order ID if available, else None.
        """
        # Truncate the payload to keep log lines bounded; replace bad
        # bytes so a broken UTF-8 payload still logs cleanly.
        payload_repr = (
            message.value[:500].decode("utf-8", errors="replace")
            if message.value
            else None
        )
        key_repr = (
            message.key.decode("utf-8", errors="replace") if message.key else None
        )
        logger.error(
            "Skipping malformed Kafka message: "
            f"error_type={type(error).__name__}, "
            f"error={error!s}, "
            f"topic={message.topic}, "
            f"partition={message.partition}, "
            f"offset={message.offset}, "
            f"key={key_repr!r}, "
            f"order_id={order_id!r}, "
            f"payload={payload_repr!r}"
        )

    async def _replay_committed_orders(self) -> None:
        """Rebuild in-memory order books by replaying committed Kafka offsets.

        Reads each assigned partition from its earliest available offset up to
        its last committed offset with downstream publishing suppressed, seeks
        back to the committed offset, and syncs rebuilt snapshots and delta
        buffers to Redis before live consumption resumes.
        """
        if self.consumer is None:
            return

        required_methods = (
            "assignment",
            "committed",
            "beginning_offsets",
            "seek",
            "getmany",
        )
        if not all(hasattr(self.consumer, method) for method in required_methods):
            return

        partitions = sorted(
            self.consumer.assignment(),
            key=lambda tp: (tp.topic, tp.partition),
        )
        # If assignment has not settled yet, trigger a brief poll before
        # checking again so startup replay never silently skips assigned work.
        # Rewind any records prefetched during assignment so fresh partitions
        # never lose their first batch.
        if not partitions:
            for _ in range(5):
                prefetched = await self.consumer.getmany(timeout_ms=200)
                for tp, records in prefetched.items():
                    if records:
                        self.consumer.seek(tp, records[0].offset)
                partitions = sorted(
                    self.consumer.assignment(),
                    key=lambda tp: (tp.topic, tp.partition),
                )
                if partitions:
                    break
        if not partitions:
            return

        beginning_offsets = await self.consumer.beginning_offsets(partitions)
        replay_targets: list[tuple[Any, int, int]] = []
        for tp in partitions:
            committed_offset = await self.consumer.committed(tp)
            beginning_offset = beginning_offsets.get(tp, 0)
            if committed_offset is None or committed_offset == 0:
                continue
            if beginning_offset > 0:
                raise RuntimeError(
                    "Cannot rebuild order books from truncated orders topic: "
                    f"topic={tp.topic}, partition={tp.partition}, "
                    f"beginning_offset={beginning_offset}, "
                    f"committed_offset={committed_offset}"
                )
            replay_targets.append((tp, 0, committed_offset))

        if not replay_targets:
            return

        replayed_count = 0
        for tp, start_offset, target_offset in replay_targets:
            self.consumer.seek(tp, start_offset)
            next_offset = start_offset
            empty_polls = 0
            while next_offset < target_offset:
                batches = await self.consumer.getmany(tp, timeout_ms=1000)
                records = batches.get(tp, [])
                if not records:
                    if hasattr(self.consumer, "position"):
                        current_pos = await self.consumer.position(tp)
                        if current_pos >= target_offset:
                            break
                    empty_polls += 1
                    if empty_polls >= 5:
                        raise RuntimeError(
                            "Startup replay stalled before reaching committed offset: "
                            f"topic={tp.topic}, partition={tp.partition}, "
                            f"next_offset={next_offset}, "
                            f"target_offset={target_offset}"
                        )
                    continue

                empty_polls = 0
                for record in records:
                    if record.offset >= target_offset:
                        next_offset = target_offset
                        break
                    await self._process_message(record, publish=False)
                    replayed_count += 1
                    next_offset = record.offset + 1

            # Restore exact fetch cursor so live consumption begins at the
            # first uncommitted offset without dropping or double-replaying.
            self.consumer.seek(tp, target_offset)

        await self.matching_engine.sync_state_to_cache()
        logger.info(
            f"Replayed {replayed_count} committed order messages across "
            f"{len(replay_targets)} partition(s) and synced Redis cache."
        )

    async def start(self) -> None:
        """Starts consuming order messages with retry logic."""
        self.consumer = AIOKafkaConsumer(
            self.topic,
            bootstrap_servers=self.bootstrap_servers,
            group_id=self.group_id,
            session_timeout_ms=self.session_timeout_ms,
            heartbeat_interval_ms=self.heartbeat_interval_ms,
            # Disable auto-commit so offsets only advance after a
            # message is handled (or deliberately skipped). Prevents
            # silent data loss when processing raises.
            enable_auto_commit=False,
            # Read from offset 0 when a fresh group has no committed offset
            # so early orders published during stack startup are not dropped.
            auto_offset_reset="earliest",
        )

        for attempt in range(self.max_retries):
            try:
                await self.consumer.start()
                logger.info(
                    f"Successfully connected to Kafka at {self.bootstrap_servers}"
                )
                break
            except Exception as e:
                if attempt == self.max_retries - 1:
                    logger.error(
                        f"Failed to connect to Kafka after {self.max_retries} "
                        f"attempts: {e!s}"
                    )
                    raise
                logger.warning(
                    "Failed to connect to Kafka (attempt "
                    f"{attempt + 1}/{self.max_retries}). "
                    f"Retrying in {self.retry_delay} seconds..."
                )
                await asyncio.sleep(self.retry_delay)

        try:
            await self._replay_committed_orders()
            async for message in self.consumer:
                await self._process_message(message)
                # Commit only after _process_message returns. Unexpected
                # exceptions skip this and bubble up, so the message is
                # reprocessed after the consumer restarts.
                await self.consumer.commit()
        except Exception as e:
            logger.error(f"Error consuming messages: {e!s}")
            raise
        finally:
            await self.stop()

    async def stop(self) -> None:
        """Stops the Kafka consumer."""
        if self.consumer:
            await self.consumer.stop()
            self.consumer = None


async def main() -> None:
    """Run the matching engine consumer service."""
    redis_client: Redis | None = None
    producer: AIOKafkaProducer | None = None
    producer_started = False
    multicast_publisher: MulticastPublisher | None = None
    consumer: OrderConsumer | None = None

    try:
        # Create Redis client for analytics.
        redis_client = Redis.from_url(
            os.getenv("REDIS_URL", "redis://redis:6379/0"), decode_responses=True
        )
        analytics = MarketDataAnalytics(redis_client)

        bootstrap_servers = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:9092")
        # Create Kafka producer for publishing market data.
        producer = AIOKafkaProducer(bootstrap_servers=bootstrap_servers)
        await producer.start()
        producer_started = True

        # Create the matching engine with Kafka producer and analytics and a
        # multicast publisher for delta messages.
        multicast_publisher = MulticastPublisher(group="239.1.1.1", port=5555)
        matching_engine = MatchingEngine(
            kafka_producer=producer,
            analytics=analytics,
            multicast_publisher=multicast_publisher,
        )
        consumer = OrderConsumer(
            matching_engine=matching_engine,
            bootstrap_servers=bootstrap_servers,
            session_timeout_ms=10000,
            heartbeat_interval_ms=1000,
        )

        await multicast_publisher.start_heartbeat_task(interval_seconds=1.0)
        await consumer.start()
        # Keep the consumer running until the user interrupts the program.
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        logger.info("Shutting down consumer...")
    finally:
        try:
            if consumer is not None:
                await consumer.stop()
            if producer is not None and producer_started:
                await producer.stop()
            if multicast_publisher is not None:
                await multicast_publisher.close()
        finally:
            try:
                if redis_client is not None:
                    await redis_client.aclose()
            finally:
                await order_book_cache.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
