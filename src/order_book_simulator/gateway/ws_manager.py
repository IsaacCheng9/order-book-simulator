from collections import defaultdict
from typing import Any

from fastapi import WebSocket


class WebSocketConnectionManager:
    def __init__(self):
        self.connections_per_ticker: dict[str, set[WebSocket]] = defaultdict(set)

    def connect(self, websocket: WebSocket, ticker: str) -> None:
        raise NotImplementedError

    def disconnect(self, websocket: WebSocket, ticker: str) -> None:
        raise NotImplementedError

    async def broadcast(self, message: dict[str, Any], ticker: str) -> None:
        raise NotImplementedError

    def get_connection_count(self, ticker: str) -> int:
        raise NotImplementedError
