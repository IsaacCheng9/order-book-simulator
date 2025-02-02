import asyncio
import json
import logging
from datetime import datetime
from typing import Any
from uuid import UUID

from aiokafka import AIOKafkaProducer
from fastapi import HTTPException


class OrderProducer:
    """Handles asynchronous production of order messages to Kafka."""

    def __init__(self, max_retries: int = 5, retry_delay: int = 5):
        self.producer = AIOKafkaProducer(
            bootstrap_servers="kafka:9092", value_serializer=self._serialise
        )
        self.topic = "orders"
        self.max_retries = max_retries
        self.retry_delay = retry_delay

    async def start(self):
        """Initialises the Kafka producer with retry logic."""
        for attempt in range(self.max_retries):
            try:
                await self.producer.start()
                logging.info("Successfully connected to Kafka")
                return
            except Exception as exception:
                if attempt == self.max_retries - 1:
                    logging.error(
                        f"Failed to connect to Kafka after {self.max_retries} attempts"
                    )
                    raise exception
                logging.warning(
                    f"Failed to connect to Kafka (attempt {attempt + 1}/{self.max_retries}). Retrying in {self.retry_delay} seconds..."
                )
                await asyncio.sleep(self.retry_delay)

    async def stop(self):
        """Cleans up the Kafka producer."""
        await self.producer.stop()

    @staticmethod
    def _serialise(data: dict[str, Any]) -> bytes:
        """Serialises order data for Kafka transmission."""

        def default(obj):
            if isinstance(obj, UUID):
                return str(obj)
            if isinstance(obj, datetime):
                return obj.isoformat()
            raise TypeError(f"Type {type(obj)} not serialisable.")

        return json.dumps(data, default=default).encode("utf-8")

    async def send_order(self, order_record: dict[str, Any]):
        """
        Publishes an order to Kafka for processing by the matching engine.

        Args:
            order_record: The order data to be published.
        """
        try:
            await self.producer.send_and_wait(self.topic, order_record)
        except Exception as e:
            # Log the error and re-raise
            # In production, you might want more sophisticated error handling
            raise HTTPException(
                status_code=500,
                detail=f"Failed to publish order to matching engine: {str(e)}.",
            )
