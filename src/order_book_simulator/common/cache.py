import os
from dataclasses import asdict
from typing import Any
from uuid import UUID

import orjson
from redis.asyncio import Redis

from order_book_simulator.common.models import MAX_DELTA_HISTORY, Delta


class OrderBookCache:
    """Manages order book data in Redis."""

    def __init__(
        self,
        redis_url: str = os.getenv("REDIS_URL", "redis://redis:6379/0"),
        max_trade_history: int = 1_000,
    ):
        """
        Creates a new order book cache.

        Uses lazy initialization for the Redis connection to allow importing
        this module without requiring a Redis server to be available.

        Args:
            redis_url: The Redis connection URL.
            max_trade_history: The maximum number of trades to keep in cache.
        """
        self._redis_url = redis_url
        self._redis: Redis | None = None
        self.max_trade_history = max_trade_history

    @property
    def redis(self) -> Redis:
        """Returns the Redis client, connecting lazily on first access."""
        if self._redis is None:
            self._redis = Redis.from_url(self._redis_url)
        return self._redis

    @redis.setter
    def redis(self, value: Redis) -> None:
        """Allows setting a mock Redis client for testing."""
        self._redis = value

    def _get_deltas_key(self, stock_id: UUID) -> str:
        """Gets the Redis key for delta history."""
        return f"deltas:{stock_id}"

    def _get_delta_seq_key(self, stock_id: UUID) -> str:
        """Gets the Redis key for the current delta sequence number."""
        return f"delta_seq:{stock_id}"

    def _get_order_book_key(self, stock_id: UUID) -> str:
        """
        Gets the Redis key for an order book.

        Args:
            stock_id: The stock ID.

        Returns:
            The Redis key for the order book.
        """
        return f"order_book:{stock_id}"

    async def set_order_book(self, stock_id: UUID, snapshot: dict[str, Any]) -> None:
        """
        Stores an order book snapshot in Redis.

        Args:
            stock_id: The stock ID.
            snapshot: The order book snapshot.
        """
        key = self._get_order_book_key(stock_id)
        await self.redis.set(
            key,
            orjson.dumps(snapshot, default=str),
        )

    async def get_order_book(self, stock_id: UUID) -> dict[str, Any] | None:
        """
        Gets an order book snapshot from Redis.

        Args:
            stock_id: The stock ID.

        Returns:
            The order book snapshot if it exists, None otherwise.
        """
        key = self._get_order_book_key(stock_id)
        raw_data = await self.redis.get(key)
        if not raw_data:
            return None
        data = (
            raw_data.decode()
            if isinstance(raw_data, (bytes, bytearray))
            else str(raw_data)
        )
        return orjson.loads(data)

    async def get_order_book_with_sequence(
        self, stock_id: UUID
    ) -> tuple[dict[str, Any] | None, int]:
        """Fetch an order book snapshot and its delta sequence atomically."""
        book_key = self._get_order_book_key(stock_id)
        seq_key = self._get_delta_seq_key(stock_id)
        if hasattr(self.redis, "mget"):
            raw_book, raw_seq = await self.redis.mget([book_key, seq_key])
        else:
            raw_book = await self.redis.get(book_key)
            raw_seq = await self.redis.get(seq_key)

        snapshot: dict[str, Any] | None = None
        if raw_book:
            data = (
                raw_book.decode()
                if isinstance(raw_book, (bytes, bytearray))
                else str(raw_book)
            )
            snapshot = orjson.loads(data)
        sequence = int(raw_seq) if raw_seq else 0
        return snapshot, sequence

    async def get_all_order_books(self) -> dict[str, dict[str, Any]]:
        """
        Gets all order book snapshots from Redis.

        Returns:
            A dictionary of order book snapshots keyed by stock ID.
        """
        keys = await self.redis.keys("order_book:*")
        result: dict[str, dict[str, Any]] = {}
        for key in keys:
            stock_id = (
                key.split(":")[-1]
                if isinstance(key, str)
                else key.decode().split(":")[-1]
            )
            raw_data = await self.redis.get(key)
            if raw_data:
                data = (
                    raw_data.decode()
                    if isinstance(raw_data, (bytes, bytearray))
                    else str(raw_data)
                )
                result[stock_id] = orjson.loads(data)
        return dict(sorted(result.items()))

    def _get_trades_key(self, stock_id: UUID) -> str:
        """Gets the Redis key for trade history."""
        return f"trades:{stock_id}"

    async def get_trades(self, stock_id: UUID, limit: int = 100) -> list[dict]:
        """
        Gets the latest trades for a stock.

        Args:
            stock_id: The stock ID.
            limit: The maximum number of trades to return.

        Returns:
            The trade history for the stock.
        """
        key = self._get_trades_key(stock_id)
        # redis-py types async commands as sync-or-async return unions.
        raw_data = await self.redis.lrange(key, -limit, -1)  # pyrefly: ignore[not-async]
        return [orjson.loads(data) for data in reversed(raw_data)]

    async def append_trades(self, stock_id: UUID, trades: list[dict]) -> None:
        """Append new trades to the stock's trade history without duplicates.

        Existing trade IDs already cached for the stock are skipped so crash
        replay of an uncommitted Kafka offset does not duplicate cached trades.

        Args:
            stock_id: The stock ID.
            trades: The list of trades to append.
        """
        if not trades:
            return

        key = self._get_trades_key(stock_id)
        existing_raw = await self.redis.lrange(key, 0, -1)  # pyrefly: ignore[not-async]
        existing_ids: set[str] = set()
        for raw_trade in existing_raw:
            parsed = orjson.loads(raw_trade)
            trade_id = parsed.get("id")
            if trade_id is not None:
                existing_ids.add(str(trade_id))

        new_trades: list[dict] = []
        for trade in trades:
            trade_id = trade.get("id")
            if trade_id is not None:
                trade_id_str = str(trade_id)
                if trade_id_str in existing_ids:
                    continue
                existing_ids.add(trade_id_str)
            new_trades.append(trade)

        if not new_trades:
            return

        serialised_trades = [orjson.dumps(trade, default=str) for trade in new_trades]
        if hasattr(self.redis, "pipeline"):
            async with self.redis.pipeline(transaction=True) as pipe:
                pipe.rpush(key, *serialised_trades)
                pipe.ltrim(key, -self.max_trade_history, -1)
                await pipe.execute()
        else:
            await self.redis.rpush(key, *serialised_trades)  # pyrefly: ignore[not-async]
            # Enforce the maximum trade history.
            await self.redis.ltrim(key, -self.max_trade_history, -1)  # pyrefly: ignore[not-async]

    async def store_deltas(self, stock_id: UUID, deltas: list[Delta]) -> None:
        """Append unseen deltas to Redis and advance the sequence watermark.

        Filters out deltas at or below the current cached sequence number so
        reprocessing an uncommitted Kafka offset is idempotent, and commits the
        list append, trim, and sequence update atomically in a Redis
        transaction.

        Args:
            stock_id: The stock ID.
            deltas: The list of deltas to store.
        """
        if not deltas:
            return

        current_seq = await self.get_current_delta_sequence_number(stock_id)
        new_deltas = [delta for delta in deltas if delta.sequence_number > current_seq]
        if not new_deltas:
            return

        # Convert each unseen Delta to a dict.
        serialised_deltas: list[bytes] = [
            orjson.dumps(asdict(delta), default=str) for delta in new_deltas
        ]
        # Store the deltas and sequence watermark atomically in Redis.
        deltas_key = self._get_deltas_key(stock_id)
        last_delta_seq_number = self._get_delta_seq_key(stock_id)
        latest_seq = new_deltas[-1].sequence_number
        if hasattr(self.redis, "pipeline"):
            async with self.redis.pipeline(transaction=True) as pipe:
                pipe.rpush(deltas_key, *serialised_deltas)
                pipe.ltrim(deltas_key, -MAX_DELTA_HISTORY, -1)
                pipe.set(last_delta_seq_number, latest_seq)
                await pipe.execute()
        else:
            await self.redis.rpush(deltas_key, *serialised_deltas)  # pyrefly: ignore[not-async]
            await self.redis.ltrim(deltas_key, -MAX_DELTA_HISTORY, -1)  # pyrefly: ignore[not-async]
            await self.redis.set(last_delta_seq_number, latest_seq)

    async def store_order_book_and_deltas(
        self,
        stock_id: UUID,
        snapshot: dict[str, Any],
        deltas: list[Delta],
    ) -> None:
        """Atomically persist an order book snapshot, unseen deltas, and seq.

        Writing the snapshot, delta list, and sequence watermark in one Redis
        transaction prevents concurrent readers of
        ``get_order_book_with_sequence`` from observing a new snapshot paired
        with a stale sequence number.

        Args:
            stock_id: The stock ID.
            snapshot: The order book snapshot to store.
            deltas: Deltas emitted by the matching step.
        """
        book_key = self._get_order_book_key(stock_id)
        serialised_snapshot = orjson.dumps(snapshot, default=str)

        new_deltas: list[Delta] = []
        if deltas:
            current_seq = await self.get_current_delta_sequence_number(stock_id)
            new_deltas = [
                delta for delta in deltas if delta.sequence_number > current_seq
            ]

        if not new_deltas:
            await self.redis.set(book_key, serialised_snapshot)
            return

        serialised_deltas: list[bytes] = [
            orjson.dumps(asdict(delta), default=str) for delta in new_deltas
        ]
        deltas_key = self._get_deltas_key(stock_id)
        seq_key = self._get_delta_seq_key(stock_id)
        latest_seq = new_deltas[-1].sequence_number
        if hasattr(self.redis, "pipeline"):
            async with self.redis.pipeline(transaction=True) as pipe:
                pipe.set(book_key, serialised_snapshot)
                pipe.rpush(deltas_key, *serialised_deltas)
                pipe.ltrim(deltas_key, -MAX_DELTA_HISTORY, -1)
                pipe.set(seq_key, latest_seq)
                await pipe.execute()
        else:
            await self.redis.set(book_key, serialised_snapshot)
            await self.redis.rpush(deltas_key, *serialised_deltas)  # pyrefly: ignore[not-async]
            await self.redis.ltrim(deltas_key, -MAX_DELTA_HISTORY, -1)  # pyrefly: ignore[not-async]
            await self.redis.set(seq_key, latest_seq)

    async def replace_order_book_and_deltas(
        self,
        stock_id: UUID,
        snapshot: dict[str, Any],
        deltas: list[Delta],
        current_sequence: int,
    ) -> None:
        """Atomically replace snapshot, deltas, and sequence after replay.

        Args:
            stock_id: The stock ID.
            snapshot: The rebuilt order book snapshot.
            deltas: Recent deltas retained in memory for the stock.
            current_sequence: Current monotonic sequence number for the stock.
        """
        book_key = self._get_order_book_key(stock_id)
        deltas_key = self._get_deltas_key(stock_id)
        seq_key = self._get_delta_seq_key(stock_id)
        serialised_snapshot = orjson.dumps(snapshot, default=str)
        bounded_deltas = deltas[-MAX_DELTA_HISTORY:] if MAX_DELTA_HISTORY > 0 else []
        serialised_deltas = [
            orjson.dumps(asdict(delta), default=str) for delta in bounded_deltas
        ]
        if hasattr(self.redis, "pipeline"):
            async with self.redis.pipeline(transaction=True) as pipe:
                pipe.set(book_key, serialised_snapshot)
                pipe.delete(deltas_key)
                if serialised_deltas:
                    pipe.rpush(deltas_key, *serialised_deltas)
                pipe.set(seq_key, current_sequence)
                await pipe.execute()
        else:
            await self.redis.set(book_key, serialised_snapshot)
            await self.redis.delete(deltas_key)
            if serialised_deltas:
                await self.redis.rpush(deltas_key, *serialised_deltas)  # pyrefly: ignore[not-async]
            await self.redis.set(seq_key, current_sequence)

    async def replace_deltas(
        self,
        stock_id: UUID,
        deltas: list[Delta],
        current_sequence: int,
    ) -> None:
        """Atomically replace cached deltas and sequence number after replay.

        Args:
            stock_id: The stock ID.
            deltas: Recent deltas retained in memory for the stock.
            current_sequence: Current monotonic sequence number for the stock.
        """
        deltas_key = self._get_deltas_key(stock_id)
        seq_key = self._get_delta_seq_key(stock_id)
        bounded_deltas = deltas[-MAX_DELTA_HISTORY:] if MAX_DELTA_HISTORY > 0 else []
        serialised_deltas = [
            orjson.dumps(asdict(delta), default=str) for delta in bounded_deltas
        ]
        if hasattr(self.redis, "pipeline"):
            async with self.redis.pipeline(transaction=True) as pipe:
                pipe.delete(deltas_key)
                if serialised_deltas:
                    pipe.rpush(deltas_key, *serialised_deltas)
                pipe.set(seq_key, current_sequence)
                await pipe.execute()
        else:
            await self.redis.delete(deltas_key)
            if serialised_deltas:
                await self.redis.rpush(deltas_key, *serialised_deltas)  # pyrefly: ignore[not-async]
            await self.redis.set(seq_key, current_sequence)

    async def publish_deltas(self, ticker: str, deltas: list[Delta]) -> None:
        """
        Publishes deltas to the WebSocket channel for the given ticker.

        Args:
            ticker: The ticker to publish the deltas to.
            deltas: The list of deltas to publish.
        """
        channel = f"ws:deltas:{ticker}"
        payload = orjson.dumps([asdict(delta) for delta in deltas], default=str)
        await self.redis.publish(channel, payload)

    async def get_deltas_since(
        self, stock_id: UUID, sequence_number: int
    ) -> list[dict[str, Any]] | None:
        """
        Retrieves deltas newer than the given sequence number from Redis.

        Args:
            stock_id: The stock ID.
            sequence_number: The last sequence number the client has seen.

        Returns:
            A list of delta dicts if recoverable, an empty list if up to date,
            or None if the requested sequence has been evicted and the client
            needs a full snapshot.
        """
        deltas_key = self._get_deltas_key(stock_id)
        all_deltas: list[bytes] = await self.redis.lrange(  # pyrefly: ignore[not-async]
            deltas_key, 0, -1
        )
        if not all_deltas:
            return []

        deserialised_deltas: list[dict[str, Any]] = [
            orjson.loads(delta) for delta in all_deltas
        ]
        if sequence_number >= deserialised_deltas[-1]["sequence_number"]:
            return []
        elif sequence_number < deserialised_deltas[0]["sequence_number"] - 1:
            return None
        return [
            delta
            for delta in deserialised_deltas
            if delta["sequence_number"] > sequence_number
        ]

    async def get_deltas_since_with_sequence(
        self, stock_id: UUID, sequence_number: int
    ) -> tuple[list[dict[str, Any]] | None, int]:
        """Fetch deltas after sequence_number and current sequence atomically."""
        deltas_key = self._get_deltas_key(stock_id)
        seq_key = self._get_delta_seq_key(stock_id)
        if hasattr(self.redis, "pipeline"):
            async with self.redis.pipeline(transaction=True) as pipe:
                pipe.lrange(deltas_key, 0, -1)
                pipe.get(seq_key)
                all_deltas, raw_seq = await pipe.execute()
        else:
            all_deltas = await self.redis.lrange(  # pyrefly: ignore[not-async]
                deltas_key, 0, -1
            )
            raw_seq = await self.redis.get(seq_key)

        current_seq = int(raw_seq) if raw_seq else 0
        if not all_deltas:
            if sequence_number == current_seq:
                return [], current_seq
            return None, current_seq

        deserialised_deltas: list[dict[str, Any]] = [
            orjson.loads(delta) for delta in all_deltas
        ]
        oldest_seq = deserialised_deltas[0]["sequence_number"]
        latest_seq = deserialised_deltas[-1]["sequence_number"]

        # Trigger snapshot fallback if Redis reset (client ahead of server),
        # deltas and seq_key disagree, or requested history was evicted.
        if (
            sequence_number > current_seq
            or latest_seq != current_seq
            or sequence_number < oldest_seq - 1
        ):
            return None, current_seq
        if sequence_number == latest_seq:
            return [], current_seq
        return (
            [
                delta
                for delta in deserialised_deltas
                if delta["sequence_number"] > sequence_number
            ],
            current_seq,
        )

    async def get_current_delta_sequence_number(self, stock_id: UUID) -> int:
        """
        Get the latest delta sequence number for a stock.

        Args:
            stock_id: The stock ID.

        Returns:
            The current sequence number, or 0 if no deltas exist.
        """
        raw_data = await self.redis.get(self._get_delta_seq_key(stock_id))
        return int(raw_data) if raw_data else 0


# Global cache instance
order_book_cache = OrderBookCache()
