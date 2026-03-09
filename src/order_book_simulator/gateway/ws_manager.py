import asyncio
from collections import defaultdict
from typing import Any

import orjson
from fastapi import WebSocket
from redis.asyncio import Redis


class ClientConnection:
    """
    Wraps a WebSocket with a bounded send queue and dedicated sender task to
    prevent slow consumers from blocking broadcast to other clients.
    """

    def __init__(self, websocket: WebSocket, max_queue_size: int = 64) -> None:
        self.websocket: WebSocket = websocket
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(
            maxsize=max_queue_size
        )
        self.sender_task: asyncio.Task = asyncio.create_task(self._send_loop())

    async def _send_loop(self) -> None:
        """
        Pulls messages from the queue and sends them to the client.

        Exits on send failure, indicating a dead connection.
        """
        while True:
            message: dict[str, Any] = await self.queue.get()
            try:
                await self.websocket.send_json(message)
            except Exception:
                break
            self.queue.task_done()

    def enqueue(self, message: dict[str, Any]) -> bool:
        """
        Non-blocking enqueue.

        If the queue is full, the client is too slow and the message is dropped.

        Args:
            message: The JSON-serialisable message to enqueue.

        Returns:
            True if the message was enqueued, False if it was dropped.
        """
        try:
            self.queue.put_nowait(message)
        except asyncio.QueueFull:
            return False
        return True

    async def close(self) -> None:
        """Cancels the sender task."""
        self.sender_task.cancel()
        try:
            await self.sender_task
        except asyncio.CancelledError:
            pass


class WebSocketConnectionManager:
    """
    Manages WebSocket connections per ticker for real-time delta fan-out.
    """

    def __init__(self) -> None:
        self.connections_per_ticker: dict[str, dict[WebSocket, ClientConnection]] = (
            defaultdict(dict)
        )

    def subscribe(self, websocket: WebSocket, ticker: str) -> None:
        """
        Subscribes a WebSocket connection to a ticker.

        Args:
            websocket: The WebSocket connection to register.
            ticker: The ticker to subscribe to.
        """
        new_connection = ClientConnection(websocket)
        self.connections_per_ticker[ticker][websocket] = new_connection

    async def unsubscribe(self, websocket: WebSocket, ticker: str) -> None:
        """
        Unsubscribes a WebSocket connection from a ticker.

        Cleans up empty ticker entries to avoid unbounded dict growth.

        Args:
            websocket: The WebSocket connection to remove.
            ticker: The ticker to unsubscribe from.
        """
        popped: ClientConnection | None = self.connections_per_ticker[ticker].pop(
            websocket, None
        )
        if popped is not None:
            await popped.close()
        if not self.connections_per_ticker[ticker]:
            del self.connections_per_ticker[ticker]

    def broadcast(self, message: dict[str, Any], ticker: str) -> None:
        """
        Enqueue a message to all clients subscribed to a ticker.

        Uses non-blocking put_nowait so broadcast time is independent
        of individual client send speeds. Messages are dropped for
        slow consumers whose queues are full.

        Args:
            message: The JSON-serialisable message to send.
            ticker: The ticker to broadcast to.
        """
        for client_connection in self.connections_per_ticker[ticker].values():
            client_connection.enqueue(message)

    async def close_all(self) -> None:
        """Closes all client connections across all tickers."""
        for clients in self.connections_per_ticker.values():
            for client in clients.values():
                await client.close()
        self.connections_per_ticker.clear()

    def get_connection_count(self, ticker: str) -> int:
        """
        Gets the number of active connections for a ticker.

        Args:
            ticker: The ticker to check.

        Returns:
            The number of active WebSocket connections.
        """
        return len(self.connections_per_ticker.get(ticker, {}))


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
            ws_manager.broadcast({"type": "deltas", "data": deltas}, ticker)
    finally:
        await pubsub.punsubscribe("ws:deltas:*")
        await redis.aclose()
