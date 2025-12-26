import asyncio
import orjson
import logging
import time
from uuid import UUID

from aiokafka import AIOKafkaConsumer

from order_book_simulator.database.connection import AsyncSessionLocal
from order_book_simulator.market_data.persistence import (
    persist_market_snapshot,
    persist_trades,
)

logger = logging.getLogger(__name__)


class MarketDataDBConsumer:
    """
    Processes incoming market data from the database and routes them to the matching engine.
    """

    def __init__(
        self,
        bootstrap_servers: str = "kafka:9092",
        topic: str = "market-data",
        group_id: str = "market-data",
        batch_size: int = 50,
        batch_timeout_ms: int = 100,
    ):
        """
        Creates a new market data DB consumer.

        Args:
            bootstrap_servers: Kafka bootstrap servers string.
            topic: Kafka topic to consume from.
            group_id: Consumer group ID.
            batch_size: Number of entries to process in each batch.
            batch_timeout_ms: Timeout for batch processing in milliseconds.
        """
        self.consumer: AIOKafkaConsumer | None = None
        self.bootstrap_servers: str = bootstrap_servers
        self.topic: str = topic
        self.group_id: str = group_id
        self.batch: list[dict] = []
        self.batch_size: int = batch_size
        self.batch_timeout: float = batch_timeout_ms / 1000.0
        self.last_flush: float = time.time()

    async def _flush_batch(self) -> None:
        if not self.batch:
            return

        try:
            async with AsyncSessionLocal() as session:
                async with session.begin():
                    for data in self.batch:
                        stock_id = UUID(data["stock_id"])
                        await persist_market_snapshot(stock_id, data, session)
                        if data.get("trades"):
                            await persist_trades(stock_id, data["trades"], session)
            logger.debug(
                f"Flushed batch of {len(self.batch)} market data entries to Postgres"
            )
        except Exception as exc:
            logger.error(f"Error flushing batch: {exc}")
            # TODO: Handle failed batch (dead letter queue, retry, etc.)

        self.batch.clear()
        self.last_flush = time.time()

    async def start(self) -> None:
        """
        Starts consuming market data from the Kafka topic and processes it in
        batches.
        """
        self.consumer = AIOKafkaConsumer(
            self.topic,
            bootstrap_servers=self.bootstrap_servers,
            group_id=self.group_id,
        )
        await self.consumer.start()
        logger.info(f"Started consuming from {self.topic} on {self.bootstrap_servers}")

        try:
            async for message in self.consumer:
                data = orjson.loads(message.value) if message.value else None
                if not data:
                    continue
                self.batch.append(data)

                should_flush = (
                    len(self.batch) >= self.batch_size
                    or time.time() - self.last_flush >= self.batch_timeout
                )
                if should_flush:
                    await self._flush_batch()
        finally:
            await self._flush_batch()
            await self.consumer.stop()
            logger.info(
                f"Stopped consuming from {self.topic} on {self.bootstrap_servers}"
            )

    async def stop(self) -> None:
        """
        Stops the Kafka consumer.
        """
        if self.consumer:
            await self.consumer.stop()


async def main():
    """Main entry point for the market data DB consumer."""
    logging.basicConfig(level=logging.INFO)
    consumer = MarketDataDBConsumer()

    try:
        await consumer.start()
    except KeyboardInterrupt:
        logger.info("Shutting down Market Data DB Consumer...")
    finally:
        await consumer.stop()


if __name__ == "__main__":
    asyncio.run(main())
