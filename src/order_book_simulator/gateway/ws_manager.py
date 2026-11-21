import asyncio
import logging
from collections import defaultdict
from collections.abc import Callable
from typing import Any

import orjson
from fastapi import WebSocket, WebSocketDisconnect
from redis.asyncio import Redis
from redis.exceptions import RedisError

from order_book_simulator.gateway.app_state import app_state

logger = logging.getLogger(__name__)

SLOW_CONSUMER_CLOSE_CODE = 1008
SLOW_CONSUMER_CLOSE_REASON = "Slow consumer; reconnect to resync"


class ClientConnection:
    """
    Wraps a WebSocket with a bounded send queue, optional bootstrap buffer,
    and dedicated sender task to prevent slow consumers from blocking broadcast
    to other clients.
    """

    def __init__(
        self,
        websocket: WebSocket,
        max_queue_size: int = 64,
        *,
        bootstrap: bool = False,
        max_bootstrap_buffer: int | None = None,
        on_disconnect: Callable[[WebSocket], None] | None = None,
    ) -> None:
        self.websocket: WebSocket = websocket
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(
            maxsize=max_queue_size
        )
        self._bootstrapping: bool = bootstrap
        self._max_bootstrap_buffer: int = (
            max_queue_size if max_bootstrap_buffer is None else max_bootstrap_buffer
        )
        self._bootstrap_buffer: list[dict[str, Any]] = []
        self._overflowed: bool = False
        self._on_disconnect: Callable[[WebSocket], None] | None = on_disconnect
        self._close_task: asyncio.Task[None] | None = None
        self.sender_task: asyncio.Task[None] = asyncio.create_task(self._send_loop())

    @property
    def is_overflowed(self) -> bool:
        """Return whether the client has overflowed and is being closed."""
        return self._overflowed

    async def _send_loop(self) -> None:
        """
        Pulls messages from the queue and sends them to the client.

        Exits on send failure, indicating a dead connection, and notifies the
        manager so the dead client does not stay registered.
        """
        while True:
            message: dict[str, Any] = await self.queue.get()
            try:
                await self.websocket.send_json(message)
            except RuntimeError, WebSocketDisconnect:
                self.queue.task_done()
                if self._on_disconnect is not None:
                    self._on_disconnect(self.websocket)
                break
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"WebSocket send failed unexpectedly: {exc}")
                self.queue.task_done()
                if self._on_disconnect is not None:
                    self._on_disconnect(self.websocket)
                break
            self.queue.task_done()

    async def _close_slow_consumer(self) -> None:
        """Disconnect an overflowing client with a recoverable close code."""
        try:
            await self.websocket.close(
                code=SLOW_CONSUMER_CLOSE_CODE,
                reason=SLOW_CONSUMER_CLOSE_REASON,
            )
        except RuntimeError, WebSocketDisconnect:
            pass

    def _handle_overflow(self) -> None:
        """Cancel sender, unregister the client, and schedule a 1008 close."""
        if self._overflowed:
            return
        self._overflowed = True
        if not self.sender_task.done():
            self.sender_task.cancel()
        if self._on_disconnect is not None:
            self._on_disconnect(self.websocket)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._close_task = loop.create_task(self._close_slow_consumer())

    def enqueue(self, message: dict[str, Any]) -> bool:
        """
        Non-blocking enqueue.

        While bootstrapping, buffers incoming messages until the initial
        snapshot is sent. On overflow, disconnects the client so it can
        reconnect and resynchronise rather than silently losing deltas.

        Args:
            message: The JSON-serialisable message to enqueue.

        Returns:
            True if the message was enqueued or buffered, False on overflow.
        """
        if self._overflowed:
            return False
        if self._bootstrapping:
            if len(self._bootstrap_buffer) >= self._max_bootstrap_buffer:
                self._handle_overflow()
                return False
            self._bootstrap_buffer.append(message)
            return True

        try:
            self.queue.put_nowait(message)
        except asyncio.QueueFull:
            self._handle_overflow()
            return False
        return True

    def complete_bootstrap(self, baseline_sequence: int) -> bool:
        """
        Flush buffered deltas newer than baseline_sequence and go live.

        Args:
            baseline_sequence: Sequence number already sent in the initial
                snapshot or catch-up response.

        Returns:
            True if live delivery started cleanly, False if the client
            overflowed during bootstrap.
        """
        if self._overflowed:
            return False

        buffered = self._bootstrap_buffer
        self._bootstrap_buffer = []
        self._bootstrapping = False

        for message in buffered:
            if message.get("type") == "deltas" and isinstance(
                message.get("data"), list
            ):
                newer = [
                    delta
                    for delta in message["data"]
                    if not isinstance(delta, dict)
                    or delta.get("sequence_number", baseline_sequence + 1)
                    > baseline_sequence
                ]
                if not newer:
                    continue
                message = {**message, "data": newer}
            if not self.enqueue(message):
                return False
        return True

    async def close(self) -> None:
        """Cancels the sender task and awaits any pending overflow close."""
        self.sender_task.cancel()
        try:
            await self.sender_task
        except asyncio.CancelledError:
            pass
        if self._close_task is not None:
            try:
                await self._close_task
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

    def _remove_connection(self, websocket: WebSocket, ticker: str) -> None:
        """Remove a closed or overflowed connection without awaiting close."""
        ticker_connections = self.connections_per_ticker.get(ticker)
        if not ticker_connections:
            return
        ticker_connections.pop(websocket, None)
        if not ticker_connections:
            del self.connections_per_ticker[ticker]

    def subscribe(
        self,
        websocket: WebSocket,
        ticker: str,
        *,
        bootstrap: bool = False,
    ) -> ClientConnection:
        """
        Subscribes a WebSocket connection to a ticker.

        Args:
            websocket: The WebSocket connection to register.
            ticker: The ticker to subscribe to.
            bootstrap: If True, buffer live messages until complete_bootstrap()
                is called after sending the initial snapshot.

        Returns:
            The registered ClientConnection.
        """
        existing = self.connections_per_ticker.get(ticker, {}).get(websocket)
        if existing is not None and not existing.sender_task.done():
            existing.sender_task.cancel()
        new_connection = ClientConnection(
            websocket,
            bootstrap=bootstrap,
            on_disconnect=lambda ws: self._remove_connection(ws, ticker),
        )
        self.connections_per_ticker[ticker][websocket] = new_connection
        return new_connection

    def complete_bootstrap(
        self, websocket: WebSocket, ticker: str, baseline_sequence: int
    ) -> bool:
        """Transition a bootstrapping client to live streaming."""
        connection = self.connections_per_ticker.get(ticker, {}).get(websocket)
        if connection is None:
            return False
        return connection.complete_bootstrap(baseline_sequence)

    async def unsubscribe(
        self,
        websocket: WebSocket,
        ticker: str,
        *,
        connection: ClientConnection | None = None,
    ) -> None:
        """
        Unsubscribes a WebSocket connection from a ticker.

        Cleans up empty ticker entries to avoid unbounded dict growth and
        always awaits close() on any passed connection that was already
        unregistered on overflow or send failure.

        Args:
            websocket: The WebSocket connection to remove.
            ticker: The ticker to unsubscribe from.
            connection: Optional ClientConnection returned by subscribe().
        """
        ticker_connections = self.connections_per_ticker.get(ticker)
        popped: ClientConnection | None = None
        if ticker_connections is not None:
            popped = ticker_connections.pop(websocket, None)
            if not ticker_connections:
                del self.connections_per_ticker[ticker]
        target = popped if popped is not None else connection
        if target is not None:
            await target.close()

    def broadcast(self, message: dict[str, Any], ticker: str) -> None:
        """
        Enqueue a message to all clients subscribed to a ticker.

        Uses non-blocking put_nowait so broadcast time is independent
        of individual client send speeds. Slow consumers whose queues fill
        are disconnected so they reconnect and resynchronise.

        Args:
            message: The JSON-serialisable message to send.
            ticker: The ticker to broadcast to.
        """
        ticker_connections = self.connections_per_ticker.get(ticker)
        if not ticker_connections:
            return
        for client_connection in list(ticker_connections.values()):
            client_connection.enqueue(message)

    async def close_all(self) -> None:
        """Closes all client connections across all tickers."""
        for clients in list(self.connections_per_ticker.values()):
            for client in list(clients.values()):
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


async def redis_pubsub_delta_subscriber(
    redis_url: str,
    reconnect_delay: float = 0.5,
    max_reconnect_delay: float = 10.0,
) -> None:
    """
    Supervised background task that subscribes to Redis Pub/Sub for delta
    notifications and broadcasts them to WebSocket clients.

    Reconnects with exponential backoff on Redis failures and updates
    app_state.redis_pubsub_healthy so /v1/health reflects fan-out liveness.

    Args:
        redis_url: The Redis connection URL.
        reconnect_delay: Initial delay in seconds before reconnecting.
        max_reconnect_delay: Upper bound in seconds for reconnect backoff.
    """
    delay = reconnect_delay
    while True:
        redis: Redis | None = None
        pubsub: Any = None
        subscribed = False
        should_backoff = False
        try:
            redis = Redis.from_url(redis_url)
            pubsub = redis.pubsub()
            await pubsub.psubscribe("ws:deltas:*")
            subscribed = True
            app_state.redis_pubsub_healthy = True
            app_state.redis_pubsub_error = None
            delay = reconnect_delay

            while True:
                message = await pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=1
                )
                if message is None:
                    continue

                if message["type"] != "pmessage":
                    continue

                channel = message["channel"]
                payload = message["data"]
                if not isinstance(channel, (str, bytes)) or not isinstance(
                    payload, (str, bytes, bytearray, memoryview)
                ):
                    continue
                try:
                    if isinstance(channel, bytes):
                        channel = channel.decode()
                    ticker = channel.split(":")[-1]
                    deltas = orjson.loads(payload)
                except (orjson.JSONDecodeError, UnicodeDecodeError) as exc:
                    logger.warning(f"Skipping malformed Redis Pub/Sub delta: {exc}")
                    continue
                ws_manager.broadcast({"type": "deltas", "data": deltas}, ticker)
        except asyncio.CancelledError:
            app_state.redis_pubsub_healthy = False
            raise
        except (RedisError, OSError) as exc:
            app_state.redis_pubsub_healthy = False
            app_state.redis_pubsub_error = str(exc)
            logger.warning(
                f"Redis Pub/Sub subscriber error: {exc}. "
                f"Reconnecting in {delay:.2f}s..."
            )
            should_backoff = True
        except Exception as exc:
            app_state.redis_pubsub_healthy = False
            app_state.redis_pubsub_error = str(exc)
            raise
        finally:
            if pubsub is not None:
                if subscribed and not should_backoff:
                    try:
                        await pubsub.punsubscribe("ws:deltas:*")
                    except (RedisError, OSError) as exc:
                        logger.debug(f"Failed to punsubscribe during cleanup: {exc}")
                if hasattr(pubsub, "aclose"):
                    try:
                        await pubsub.aclose()
                    except (RedisError, OSError) as exc:
                        logger.debug(f"Failed to close pubsub during cleanup: {exc}")
            if redis is not None:
                try:
                    await redis.aclose()
                except (RedisError, OSError) as exc:
                    logger.debug(f"Failed to close Redis client during cleanup: {exc}")

        if should_backoff:
            await asyncio.sleep(delay)
            delay = min(delay * 2, max_reconnect_delay)
