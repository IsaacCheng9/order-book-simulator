import asyncio
import json
import logging
from typing import Any

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
        max_batch_size: int = 8192,
        linger_ms: int = 0,
    ):
        """
        Creates a new order producer.

        Args:
            bootstrap_servers: Kafka bootstrap servers string.
            topic: Kafka topic to produce to.
            max_retries: Maximum number of connection attempts.
            retry_delay: Delay between connection attempts in seconds.
            max_batch_size: Maximum size of buffered records in bytes.
            linger_ms: Time to wait for more records before sending a batch.
        """
        self.producer: AIOKafkaProducer | None = None
        self.bootstrap_servers = bootstrap_servers
        self.topic = topic
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.max_batch_size = max_batch_size
        self.linger_ms = linger_ms

    async def start(self) -> None:
        """
        Initialises the Kafka producer with retry logic.

        Raises:
            Exception: If connection to Kafka fails after max retries.
        """
        self.producer = AIOKafkaProducer(
            bootstrap_servers=self.bootstrap_servers,
            linger_ms=self.linger_ms,
            max_batch_size=self.max_batch_size,
            compression_type=None,
            acks="all",
            enable_idempotence=True,
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

    async def send_order(self, order_record: dict[str, Any]) -> None:
        """Publishes an order to Kafka for processing by the matching engine."""
        if not self.producer:
            raise HTTPException(
                status_code=503,
                detail="Order processing service is unavailable",
            )

        # Convert types before serialising.
        kafka_record = {
            "id": str(order_record["id"]),
            "instrument_id": str(order_record["instrument_id"]),
            "type": order_record["type"].value,
            "side": order_record["side"].value,
            "price": str(order_record["price"]) if order_record["price"] else None,
            "quantity": str(order_record["quantity"]),
            "gateway_received_at": order_record["gateway_received_at"],
        }

        logger.info(
            f"Publishing order: id={kafka_record['id']}, "
            f"type={kafka_record['type']}, side={kafka_record['side']}, "
            f"price={kafka_record['price']}, quantity={kafka_record['quantity']}"
        )

        try:
            await self.producer.send_and_wait(
                self.topic,
                json.dumps(kafka_record).encode("utf-8"),
            )
            logger.info(f"Order {kafka_record['id']} successfully published to Kafka")
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
                self.topic,
                json.dumps({"type": "health_check"}).encode("utf-8"),
                partition=0,
            )
            return True
        except Exception as e:
            logger.error(f"Kafka health check failed: {e}")
            return False
