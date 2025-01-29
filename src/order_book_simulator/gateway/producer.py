from aiokafka import AIOKafkaProducer
import json
from typing import Any
from uuid import UUID
from datetime import datetime

from fastapi import HTTPException


class OrderProducer:
    """Handles asynchronous production of order messages to Kafka."""

    def __init__(self):
        self.producer = AIOKafkaProducer(
            bootstrap_servers="kafka:9092", value_serializer=self._serialise
        )
        self.topic = "orders"

    async def start(self):
        """Initialises the Kafka producer."""
        await self.producer.start()

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
