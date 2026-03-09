from collections import defaultdict
from typing import Any

from fastapi import WebSocket


class WebSocketConnectionManager:
    """
    Manages WebSocket connections per ticker for real-time delta fan-out.
    """

    def __init__(self) -> None:
        self.connections_per_ticker: dict[str, set[WebSocket]] = defaultdict(set)

    def connect(self, websocket: WebSocket, ticker: str) -> None:
        """
        Registers a WebSocket connection for a ticker.

        Args:
            websocket: The WebSocket connection to register.
            ticker: The ticker to subscribe to.
        """
        self.connections_per_ticker[ticker].add(websocket)

    def disconnect(self, websocket: WebSocket, ticker: str) -> None:
        """
        Removes a WebSocket connection for a ticker.

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
            self.disconnect(connection, ticker)

    def get_connection_count(self, ticker: str) -> int:
        """
        Gets the number of active connections for a ticker.

        Args:
            ticker: The ticker to check.

        Returns:
            The number of active WebSocket connections.
        """
        return len(self.connections_per_ticker.get(ticker, set()))
