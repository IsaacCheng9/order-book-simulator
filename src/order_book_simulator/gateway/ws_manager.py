from collections import defaultdict
from typing import Any

from fastapi import WebSocket


class WebSocketConnectionManager:
    def __init__(self):
        self.connections_per_ticker: dict[str, set[WebSocket]] = defaultdict(set)

    def connect(self, websocket: WebSocket, ticker: str) -> None:
        self.connections_per_ticker[ticker].add(websocket)

    def disconnect(self, websocket: WebSocket, ticker: str) -> None:
        self.connections_per_ticker[ticker].remove(websocket)
        # Clean up empty ticker entries to avoid unbounded dict growth.
        if not self.connections_per_ticker[ticker]:
            del self.connections_per_ticker[ticker]

    async def broadcast(self, message: dict[str, Any], ticker: str) -> None:
        dead_connections: list[WebSocket] = []

        # Iterate over a copy of the set to avoid changing the set while
        # iterating, which would raise a RuntimeError.
        for connection in self.connections_per_ticker[ticker].copy():
            try:
                await connection.send_json(message)
            except Exception:
                dead_connections.append(connection)

        for connection in dead_connections:
            self.disconnect(connection, ticker)

    def get_connection_count(self, ticker: str) -> int:
        return len(self.connections_per_ticker.get(ticker, set()))
