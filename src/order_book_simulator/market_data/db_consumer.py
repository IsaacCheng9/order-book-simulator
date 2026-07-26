import asyncio
import logging
import time
from typing import Any
from uuid import UUID

import orjson
from aiokafka import AIOKafkaConsumer, ConsumerRecord, TopicPartition

from order_book_simulator.database.connection import AsyncSessionLocal
from order_book_simulator.market_data.persistence import (
    persist_market_snapshot,
    persist_trades,
)

logger = logging.getLogger(__name__)

MALFORMED_MESSAGE_ERRORS = (orjson.JSONDecodeError, TypeError)


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
        max_flush_retries: int = 3,
        retry_backoff_ms: int = 100,
    ):
        """
        Creates a new market data DB consumer.

        Args:
            bootstrap_servers: Kafka bootstrap servers string.
            topic: Kafka topic to consume from.
            group_id: Consumer group ID.
            batch_size: Number of entries to process in each batch.
            batch_timeout_ms: Timeout for batch processing in milliseconds.
            max_flush_retries: Maximum attempts to persist a failed batch.
            retry_backoff_ms: Delay between failed persistence attempts.
        """
        self.consumer: AIOKafkaConsumer | None = None
        self.bootstrap_servers: str = bootstrap_servers
        self.topic: str = topic
        self.group_id: str = group_id
        self.batch: list[dict[str, Any]] = []
        self.pending_offsets: dict[TopicPartition, int] = {}
        self.batch_size: int = batch_size
        self.batch_timeout: float = batch_timeout_ms / 1000.0
        self.max_flush_retries: int = max_flush_retries
        self.retry_backoff: float = retry_backoff_ms / 1000.0
        self.last_flush: float = time.time()

    async def _flush_batch(self) -> None:
        if not self.batch:
            return

        batch = list(self.batch)
        offsets = dict(self.pending_offsets)
        for attempt in range(1, self.max_flush_retries + 1):
            try:
                await self._persist_batch(batch)
                break
            except Exception as exc:
                if attempt >= self.max_flush_retries:
                    logger.exception(
                        "Failed to flush market data batch after "
                        f"{self.max_flush_retries} attempts"
                    )
                    raise
                logger.warning(
                    "Failed to flush market data batch "
                    f"(attempt {attempt}/{self.max_flush_retries}): {exc}. "
                    f"Retrying in {self.retry_backoff:.3f} seconds..."
                )
                await asyncio.sleep(self.retry_backoff)

        await self._commit_offsets(offsets)
        self.batch.clear()
        self.pending_offsets.clear()
        self.last_flush = time.time()
        logger.debug(f"Flushed batch of {len(batch)} market data entries to Postgres")

    async def _persist_batch(self, batch: list[dict[str, Any]]) -> None:
        async with AsyncSessionLocal() as session, session.begin():
            for data in batch:
                stock_id = UUID(data["stock_id"])
                await persist_market_snapshot(stock_id, data, session)
                if data.get("trades"):
                    await persist_trades(stock_id, data["trades"], session)

    async def _commit_offsets(self, offsets: dict[TopicPartition, int]) -> None:
        if not offsets or self.consumer is None:
            return

        await self.consumer.commit(offsets)

    async def _commit_message_offset(self, message: ConsumerRecord) -> None:
        topic_partition = TopicPartition(message.topic, message.partition)
        await self._commit_offsets({topic_partition: message.offset + 1})

    def _add_to_batch(
        self,
        data: dict[str, Any],
        message: ConsumerRecord,
    ) -> None:
        self.batch.append(data)
        topic_partition = TopicPartition(message.topic, message.partition)
        next_offset = message.offset + 1
        self.pending_offsets[topic_partition] = max(
            next_offset,
            self.pending_offsets.get(topic_partition, 0),
        )

    def _should_flush(self) -> bool:
        return bool(self.batch) and (
            len(self.batch) >= self.batch_size
            or time.time() - self.last_flush >= self.batch_timeout
        )

    def _decode_message(self, message: ConsumerRecord) -> dict[str, Any] | None:
        if not message.value:
            return None

        data = orjson.loads(message.value)
        if not isinstance(data, dict):
            raise TypeError("Market data message payload must be a JSON object")
        return data

    def _log_skipped_message(
        self,
        message: ConsumerRecord,
        error: Exception,
    ) -> None:
        payload_repr = (
            message.value[:500].decode("utf-8", errors="replace")
            if message.value
            else None
        )
        logger.error(
            "Skipping malformed market data Kafka message: "
            f"error_type={type(error).__name__}, "
            f"error={error!s}, "
            f"topic={message.topic}, "
            f"partition={message.partition}, "
            f"offset={message.offset}, "
            f"payload={payload_repr!r}"
        )

    async def start(self) -> None:
        """
        Starts consuming market data from the Kafka topic and processes it in
        batches.
        """
        self.consumer = AIOKafkaConsumer(
            self.topic,
            bootstrap_servers=self.bootstrap_servers,
            group_id=self.group_id,
            enable_auto_commit=False,
        )
        await self.consumer.start()
        logger.info(f"Started consuming from {self.topic} on {self.bootstrap_servers}")

        try:
            while True:
                records = await self.consumer.getmany(
                    timeout_ms=max(1, int(self.batch_timeout * 1000)),
                    max_records=self.batch_size,
                )

                for messages in records.values():
                    for message in messages:
                        try:
                            data = self._decode_message(message)
                        except MALFORMED_MESSAGE_ERRORS as exc:
                            self._log_skipped_message(message, exc)
                            await self._commit_message_offset(message)
                            continue

                        if data is None:
                            await self._commit_message_offset(message)
                            continue

                        self._add_to_batch(data, message)
                        if self._should_flush():
                            await self._flush_batch()

                if self._should_flush():
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
