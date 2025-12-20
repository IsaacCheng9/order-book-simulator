import json
from typing import Any
from uuid import UUID

import redis
from redis import Redis


class OrderBookCache:
    """Manages order book data in Redis."""

    def __init__(self, redis_url: str = "redis://redis:6379/0"):
        """
        Creates a new order book cache.

        Args:
            redis_url: The Redis connection URL.
        """
        self.redis: Redis = redis.from_url(redis_url)

    def _get_order_book_key(self, stock_id: UUID) -> str:
        """
        Gets the Redis key for an order book.

        Args:
            stock_id: The stock ID.

        Returns:
            The Redis key for the order book.
        """
        return f"order_book:{stock_id}"

    def set_order_book(self, stock_id: UUID, snapshot: dict[str, Any]) -> None:
        """
        Stores an order book snapshot in Redis.

        Args:
            stock_id: The stock ID.
            snapshot: The order book snapshot.
        """
        key = self._get_order_book_key(stock_id)
        self.redis.set(key, json.dumps(snapshot, default=str))  # Use str for Decimal

    def get_order_book(self, stock_id: UUID) -> dict[str, Any] | None:
        """
        Gets an order book snapshot from Redis.

        Args:
            stock_id: The stock ID.

        Returns:
            The order book snapshot if it exists, None otherwise.
        """
        key = self._get_order_book_key(stock_id)
        raw_data = self.redis.get(key)
        if not raw_data:
            return None
        data = (
            raw_data.decode()
            if isinstance(raw_data, (bytes, bytearray))
            else str(raw_data)
        )
        return json.loads(data)

    def get_all_order_books(self) -> dict[str, dict[str, Any]]:
        """
        Gets all order book snapshots from Redis.

        Returns:
            A dictionary of order book snapshots keyed by stock ID.
        """
        keys = self.redis.keys("order_book:*")
        result: dict[str, dict[str, Any]] = {}
        for key in keys:  # type: ignore
            stock_id = (
                key.split(":")[-1]
                if isinstance(key, str)
                else key.decode().split(":")[-1]
            )
            raw_data = self.redis.get(key)
            if raw_data:
                data = (
                    raw_data.decode()
                    if isinstance(raw_data, (bytes, bytearray))
                    else str(raw_data)
                )
                result[stock_id] = json.loads(data)  # type: ignore
        return dict(sorted(result.items()))

    def _get_trades_key(self, stock_id: UUID) -> str:
        """Gets the Redis key for trade history."""
        return f"trades:{stock_id}"

    def get_trades(self, stock_id: UUID, limit: int = 100) -> list[dict]:
        """
        Gets the latest trades for a stock.

        Args:
            stock_id: The stock ID.
            limit: The maximum number of trades to return.

        Returns:
            The trade history for the stock.
        """
        key = self._get_trades_key(stock_id)
        raw_data = self.redis.lrange(key, -limit, -1)
        return [json.loads(data) for data in reversed(raw_data)]  # type: ignore

    def append_trades(self, stock_id: UUID, trades: list[dict]) -> bool:
        """
        Appends trades to the trade history for a stock.

        We batch the trades into a single Redis command to reduce the number of
        round trips to Redis.

        Args:
            stock_id: The stock ID.
            trades: The list of trades to append.

        Returns:
            True if the trades were appended successfully.
        """
        key = self._get_trades_key(stock_id)
        serialised_trades = [json.dumps(trade, default=str) for trade in trades]
        self.redis.rpush(key, *serialised_trades)
        # Only keep the last 1000 trades.
        self.redis.ltrim(key, -1000, -1)

        return True


# Global cache instance
order_book_cache = OrderBookCache()
