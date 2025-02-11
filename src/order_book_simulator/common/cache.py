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
        data = self.redis.get(key)
        return json.loads(data) if data else None  # type: ignore

    def get_all_order_books(self) -> dict[str, dict[str, Any]]:
        """
        Gets all order book snapshots from Redis.

        Returns:
            A dictionary of order book snapshots keyed by stock ID.
        """
        keys = self.redis.keys("order_book:*")
        result = {}
        for key in keys:  # type: ignore
            # Handle string keys from mock Redis
            stock_id = (
                key.split(":")[-1]
                if isinstance(key, str)
                else key.decode().split(":")[-1]
            )
            data = self.redis.get(key)
            if data:
                result[stock_id] = json.loads(data)  # type: ignore
        return dict(sorted(result.items()))


# Global cache instance
order_book_cache = OrderBookCache()
