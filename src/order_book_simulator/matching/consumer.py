import json
import asyncio
import logging

from aiokafka import AIOKafkaConsumer, ConsumerRecord

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
        """
        self.consumer: AIOKafkaConsumer | None = None
        self.matching_engine = matching_engine
        self.bootstrap_servers = bootstrap_servers
        self.topic = topic
        self.group_id = group_id
        self.max_retries = max_retries
        self.retry_delay = retry_delay

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
            await self.matching_engine.process_order(order_data)
        except Exception as e:
            logger.error(f"Error processing message: {e}")

    async def start(self) -> None:
        """Starts consuming order messages with retry logic."""
        self.consumer = AIOKafkaConsumer(
            self.topic,
            bootstrap_servers=self.bootstrap_servers,
            group_id=self.group_id,
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
