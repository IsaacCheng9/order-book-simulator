from dataclasses import asdict
import orjson
from typing import Any
from uuid import UUID

from redis.asyncio import Redis

from order_book_simulator.common.models import MAX_DELTA_HISTORY, Delta


class OrderBookCache:
    """Manages order book data in Redis."""

    def __init__(
        self, redis_url: str = "redis://redis:6379/0", max_trade_history: int = 1_000
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
        raw_data = await self.redis.lrange(key, -limit, -1)  # type: ignore[arg-type]
        return [orjson.loads(data) for data in reversed(raw_data)]

    async def append_trades(self, stock_id: UUID, trades: list[dict]) -> None:
        """
        Appends trades to the trade history for a stock.

        We batch the trades into a single Redis command to reduce the number of
        round trips to Redis.

        Args:
            stock_id: The stock ID.
            trades: The list of trades to append.
        """
        key = self._get_trades_key(stock_id)
        serialised_trades = [orjson.dumps(trade, default=str) for trade in trades]
        await self.redis.rpush(key, *serialised_trades)  # type: ignore[arg-type]
        # Enforce the maximum trade history.
        await self.redis.ltrim(key, -self.max_trade_history, -1)  # type: ignore[arg-type]

    async def store_deltas(self, stock_id: UUID, deltas: list[Delta]) -> None:
        """
        Appends deltas to the Redis delta list and update the current sequence
        number. Trims to MAX_DELTA_HISTORY to bound memory usage.

        Args:
            stock_id: The stock ID.
            deltas: The list of deltas to store.
        """
        if not deltas:
            return

        # Convert each Delta to a dict.
        serialised_deltas: list[bytes] = [
            orjson.dumps(asdict(delta), default=str) for delta in deltas
        ]
        # Store the deltas in Redis.
        deltas_key = self._get_deltas_key(stock_id)
        await self.redis.rpush(deltas_key, *serialised_deltas)  # type: ignore[arg-type]
        await self.redis.ltrim(deltas_key, -MAX_DELTA_HISTORY, -1)  # type: ignore[arg-type]
        last_delta_seq_number = self._get_delta_seq_key(stock_id)
        await self.redis.set(last_delta_seq_number, deltas[-1].sequence_number)

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
        all_deltas: list[bytes] = await self.redis.lrange(deltas_key, 0, -1)  # type: ignore[arg-type]
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
