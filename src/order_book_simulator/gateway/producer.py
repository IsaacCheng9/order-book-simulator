import asyncio
import json
import logging
from datetime import datetime
from typing import Any
from uuid import UUID

from aiokafka import AIOKafkaProducer
from fastapi import HTTPException

logger = logging.getLogger(__name__)


class OrderProducer:
    """Handles asynchronous production of order messages to Kafka."""

    def __init__(
        self,
        bootstrap_servers: str = "kafka:9092",
        topic: str = "orders",
        max_retries: int = 5,
        retry_delay: int = 5,
        batch_size: int = 16384,
        linger_ms: int = 100,
    ):
        """
        Creates a new order producer.

        Args:
            bootstrap_servers: Kafka bootstrap servers string.
            topic: Kafka topic to produce to.
            max_retries: Maximum number of connection attempts.
            retry_delay: Delay between connection attempts in seconds.
            batch_size: Maximum size of buffered records in bytes.
            linger_ms: Time to wait for more records before sending a batch.
        """
        self.producer: AIOKafkaProducer | None = None
        self.bootstrap_servers = bootstrap_servers
        self.topic = topic
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.batch_size = batch_size
        self.linger_ms = linger_ms

    async def start(self) -> None:
        """
        Initialises the Kafka producer with retry logic.

        Raises:
            Exception: If connection to Kafka fails after max retries.
        """
        self.producer = AIOKafkaProducer(
            bootstrap_servers=self.bootstrap_servers,
            value_serializer=self._serialise,
            linger_ms=self.linger_ms,
            acks="all",  # Ensure durability.
            enable_idempotence=True,  # Prevent duplicate messages.
        )

        for attempt in range(self.max_retries):
            try:
                await self.producer.start()
                logger.info(
                    f"Successfully connected to Kafka at {self.bootstrap_servers}"
                )
                return
            except Exception as e:
                if attempt == self.max_retries - 1:
                    logger.error(
                        f"Failed to connect to Kafka after {self.max_retries} "
                        f"attempts: {e}"
                    )
                    raise
                logger.warning(
                    "Failed to connect to Kafka (attempt "
                    f"{attempt + 1}/{self.max_retries}). "
                    f"Retrying in {self.retry_delay} seconds..."
                )
                await asyncio.sleep(self.retry_delay)

    async def stop(self) -> None:
        """Cleans up the Kafka producer."""
        if self.producer:
            await self.producer.stop()
            self.producer = None
            logger.info("Kafka producer stopped")

    @staticmethod
    def _serialise(data: dict[str, Any]) -> bytes:
        """
        Serialises order data for Kafka transmission.

        Args:
            data: Dictionary containing order data.

        Returns:
            JSON-encoded bytes of the order data.

        Raises:
            TypeError: If an unserialisable type is encountered.
        """

        def default(obj: Any) -> str:
            if isinstance(obj, UUID):
                return str(obj)
            if isinstance(obj, datetime):
                return obj.isoformat()
            raise TypeError(f"Type {type(obj)} not serialisable")

        return json.dumps(data, default=default).encode("utf-8")

    async def send_order(self, order_record: dict[str, Any]) -> None:
        """
        Publishes an order to Kafka for processing by the matching engine.

        Args:
            order_record: The order data to be published.

        Raises:
            HTTPException: If publishing fails or producer is not initialized.
        """
        if not self.producer:
            logger.error("Attempted to send order with uninitialised producer")
            raise HTTPException(
                status_code=503,
                detail="Order processing service is unavailable",
            )

        try:
            await self.producer.send_and_wait(
                self.topic,
                order_record,
                partition=hash(str(order_record.get("instrument_id"))) % 3,
            )
            logger.info(
                f"Order {order_record.get('id', 'unknown')} successfully published to "
                "Kafka"
            )
        except Exception as e:
            logger.error(f"Failed to publish order to Kafka: {e}")
            raise HTTPException(
                status_code=500,
                detail=f"Failed to publish order to matching engine: {e}",
            )

    async def check_health(self) -> bool:
        """
        Checks if the producer is healthy by attempting to send a test message.

        Returns:
            bool: True if producer is healthy, False otherwise.
        """
        if not self.producer:
            return False

        try:
            await self.producer.send_and_wait(
                self.topic, value={"type": "health_check"}, partition=0
            )
            return True
        except Exception as e:
            logger.error(f"Kafka health check failed: {e}")
            return False
