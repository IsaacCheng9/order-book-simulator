import asyncio
import orjson
from redis.asyncio import Redis
from collections import defaultdict
from typing import Any

from fastapi import WebSocket


class WebSocketConnectionManager:
    """
    Manages WebSocket connections per ticker for real-time delta fan-out.
    """

    def __init__(self) -> None:
        self.connections_per_ticker: dict[str, set[WebSocket]] = defaultdict(set)

    def subscribe(self, websocket: WebSocket, ticker: str) -> None:
        """
        Subscribes a WebSocket connection to a ticker.

        Args:
            websocket: The WebSocket connection to register.
            ticker: The ticker to subscribe to.
        """
        self.connections_per_ticker[ticker].add(websocket)

    def unsubscribe(self, websocket: WebSocket, ticker: str) -> None:
        """
        Unsubscribes a WebSocket connection from a ticker.

        Cleans up empty ticker entries to avoid unbounded dict growth.

        Args:
            websocket: The WebSocket connection to remove.
            ticker: The ticker to unsubscribe from.
        """
        self.connections_per_ticker[ticker].remove(websocket)
        if not self.connections_per_ticker[ticker]:
            del self.connections_per_ticker[ticker]

    async def broadcast(self, message: dict[str, Any], ticker: str) -> None:
        """
        Sends a message to all clients subscribed to a ticker.

        Dead connections are detected and cleaned up without blocking other
        clients.

        Args:
            message: The JSON-serialisable message to send.
            ticker: The ticker to broadcast to.
        """
        dead_connections: list[WebSocket] = []

        # Iterate over a copy to avoid RuntimeError from set
        # mutation during iteration.
        for connection in self.connections_per_ticker[ticker].copy():
            try:
                await connection.send_json(message)
            except Exception:
                dead_connections.append(connection)

        for connection in dead_connections:
            self.unsubscribe(connection, ticker)

    def get_connection_count(self, ticker: str) -> int:
        """
        Gets the number of active connections for a ticker.

        Args:
            ticker: The ticker to check.

        Returns:
            The number of active WebSocket connections.
        """
        return len(self.connections_per_ticker.get(ticker, set()))


ws_manager = WebSocketConnectionManager()


async def redis_pubsub_delta_subscriber(redis_url: str) -> None:
    """
    Background task that subscribes to Redis Pub/Sub for delta notifications
    and broadcasts them to WebSocket clients.

    Args:
        redis_url: The Redis connection URL.
    """
    redis = Redis.from_url(redis_url)
    pubsub = redis.pubsub()

    try:
        await pubsub.psubscribe("ws:deltas:*")

        while True:
            message = await pubsub.get_message(
                ignore_subscribe_messages=True, timeout=1
            )
            if message is None:
                continue

            channel = message["channel"]
            if isinstance(channel, bytes):
                channel = channel.decode()
            ticker = channel.split(":")[-1]
            deltas = orjson.loads(message["data"])
            await ws_manager.broadcast({"type": "deltas", "data": deltas}, ticker)
    finally:
        await pubsub.punsubscribe("ws:deltas:*")
        await redis.aclose()


class ClientConnection:
    def __init__(self, websocket: WebSocket, max_queue_size: int = 64):
        self.websocket = websocket
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(
            maxsize=max_queue_size
        )
        self.sender_task: asyncio.Task = asyncio.create_task(self._send_loop())

    async def _send_loop(self) -> None:
        pass

    def enqueue(self, message: dict[str, Any]) -> bool:
        pass

    async def close(self) -> None:
        self.sender_task.cancel()
        try:
            await self.sender_task
        except asyncio.CancelledError:
            pass
        self.websocket.close()
