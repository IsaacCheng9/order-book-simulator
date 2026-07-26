import asyncio
import logging
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

import orjson
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer, ConsumerRecord
from redis.asyncio import Redis

from order_book_simulator.common.models import OrderSide, OrderType
from order_book_simulator.market_data.analytics import MarketDataAnalytics
from order_book_simulator.matching.matching_engine import MatchingEngine
from order_book_simulator.multicast.multicast_publisher import MulticastPublisher

logger = logging.getLogger(__name__)

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
        bootstrap_servers: str = "kafka:9092",
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
            bootstrap_servers: Kafka bootstrap servers string.
            topic: Kafka topic to consume from.
            group_id: Consumer group ID.
            max_retries: Maximum number of connection attempts.
            retry_delay: Delay between connection attempts in seconds.
            session_timeout_ms: Kafka session timeout in milliseconds.
            heartbeat_interval_ms: Kafka heartbeat interval in milliseconds.
        """
        self.consumer: AIOKafkaConsumer | None = None
        self.matching_engine = matching_engine
        self.bootstrap_servers = bootstrap_servers
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
        self._validate_uuid(self._require_string(order_data, "id"), "id")
        self._validate_uuid(self._require_string(order_data, "stock_id"), "stock_id")
        self._require_string(order_data, "ticker")
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
        self._validate_uuid(self._require_string(order_data, "order_id"), "order_id")
        self._validate_uuid(self._require_string(order_data, "stock_id"), "stock_id")
        self._require_string(order_data, "ticker")

    async def _process_message(self, message: ConsumerRecord) -> None:
        """
        Processes a single message from Kafka.

        Malformed or unknown messages are logged with full context and
        skipped. Unexpected errors propagate so the consumer crashes
        rather than processing bad state silently.

        Args:
            message: The message received from Kafka.
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
            logger.info(f"Processing cancellation: {order_id=}")
            result = await self.matching_engine.cancel_order(order_data)
            logger.info(f"Cancelled order {order_id=}: {result}")
            return

        if message_type == "order":
            if gateway_time is None:
                raise RuntimeError("Order message was parsed without gateway time.")
            # Track the latency for new orders.
            kafka_latency = (datetime.now(UTC) - gateway_time).total_seconds() * 1000
            logger.info(
                f"Processing order: {order_id=}, kafka_latency={kafka_latency:.2f}ms"
            )

            start_process = datetime.now(UTC)
            await self.matching_engine.process_order(order_data)
            process_time = (datetime.now(UTC) - start_process).total_seconds() * 1000
            total_latency = (datetime.now(UTC) - gateway_time).total_seconds() * 1000
            logger.info(
                f"Order processed: {order_id=}, "
                f"matching_time={process_time:.2f}ms, "
                f"total_latency={total_latency:.2f}ms"
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


async def main():
    """Main entry point for the matching engine consumer."""
    # Create Redis client for analytics.
    redis_client = Redis(host="redis", port=6379, decode_responses=True)
    analytics = MarketDataAnalytics(redis_client)

    # Create Kafka producer for publishing market data.
    producer = AIOKafkaProducer(bootstrap_servers="kafka:9092")
    await producer.start()

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
        session_timeout_ms=10000,
        heartbeat_interval_ms=1000,
    )

    try:
        await multicast_publisher.start_heartbeat_task(interval_seconds=1.0)
        await consumer.start()
        # Keep the consumer running until the user interrupts the program.
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        logger.info("Shutting down consumer...")
    finally:
        await consumer.stop()
        await producer.stop()
        await multicast_publisher.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
