import asyncio
import json
import logging
from datetime import datetime, timezone

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer, ConsumerRecord
from redis.asyncio import Redis

from order_book_simulator.market_data.analytics import MarketDataAnalytics
from order_book_simulator.matching.engine import MatchingEngine

logger = logging.getLogger(__name__)


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

    async def _process_message(self, message: ConsumerRecord) -> None:
        """
        Processes a single message from Kafka.

        Args:
            message: The message received from Kafka.
        """
        try:
            if message.value is None:
                return
            order_data = json.loads(message.value.decode("utf-8"))
            # Skip health check messages
            if order_data.get("type") == "health_check":
                return

            gateway_time = datetime.fromisoformat(order_data["gateway_received_at"])
            kafka_latency = (
                datetime.now(timezone.utc) - gateway_time
            ).total_seconds() * 1000

            logger.info(
                f"Processing order: id={order_data['id']}, "
                f"kafka_latency={kafka_latency:.2f}ms"
            )

            start_process = datetime.now(timezone.utc)
            await self.matching_engine.process_order(order_data)
            process_time = (
                datetime.now(timezone.utc) - start_process
            ).total_seconds() * 1000

            total_latency = (
                datetime.now(timezone.utc) - gateway_time
            ).total_seconds() * 1000

            logger.info(
                f"Order processed: id={order_data['id']}, "
                f"matching_time={process_time:.2f}ms, "
                f"total_latency={total_latency:.2f}ms"
            )
        except Exception as e:
            logger.error(f"Error processing message: {e}")

    async def start(self) -> None:
        """Starts consuming order messages with retry logic."""
        self.consumer = AIOKafkaConsumer(
            self.topic,
            bootstrap_servers=self.bootstrap_servers,
            group_id=self.group_id,
            session_timeout_ms=self.session_timeout_ms,
            heartbeat_interval_ms=self.heartbeat_interval_ms,
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
                        f"attempts: {str(e)}"
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
        except Exception as e:
            logger.error(f"Error consuming messages: {str(e)}")
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

    # Create the matching engine with Kafka producer and analytics.
    matching_engine = MatchingEngine(
        kafka_producer=producer,
        analytics=analytics,
    )
    consumer = OrderConsumer(
        matching_engine=matching_engine,
        session_timeout_ms=10000,
        heartbeat_interval_ms=1000,
    )

    try:
        await consumer.start()
        # Keep the consumer running until the user interrupts the program.
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        logger.info("Shutting down consumer...")
    finally:
        await consumer.stop()
        await producer.stop()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
