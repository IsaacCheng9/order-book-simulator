from typing import Any

from fastapi import APIRouter, WebSocket
from starlette.websockets import WebSocketDisconnect

from order_book_simulator.common.cache import order_book_cache
from order_book_simulator.database.connection import AsyncSessionLocal
from order_book_simulator.database.queries import get_stock_by_ticker
from order_book_simulator.gateway.ws_manager import ws_manager

ws_router = APIRouter()


@ws_router.websocket("/{ticker}")
async def order_book_ws(
    websocket: WebSocket, ticker: str, last_seen_sequence_number: int | None = None
) -> None:
    """
    Handles a WebSocket connection for real-time order book updates.

    Validates the ticker, then either recovers missed deltas (if the client
    provides a last seen sequence number) or sends a full snapshot as a
    starting point. Falls back to a snapshot if the requested sequence has been
    evicted from the buffer. Holds the connection open for live delta broadcasts
    and cleans up the subscription on disconnect.

    Args:
        websocket: The WebSocket connection.
        ticker: The ticker to subscribe to.
        last_seen_sequence_number: The last sequence number the client has
            seen. If provided, only missed deltas are sent.
    """
    await websocket.accept()

    # Validate the ticker against the DB.
    async with AsyncSessionLocal() as db:
        stock = await get_stock_by_ticker(ticker, db)
    if not stock:
        await websocket.close(code=1008, reason=f"Ticker {ticker} not found")
        return

    send_snapshot = True
    # If the client has provided a last sequence number and we can get the
    # deltas since then, send the deltas to save bandwidth.
    if last_seen_sequence_number is not None:
        deltas: list[dict[str, Any]] | None = await order_book_cache.get_deltas_since(
            stock.id, last_seen_sequence_number
        )
        if deltas is not None:
            current_sequence_number = (
                await order_book_cache.get_current_delta_sequence_number(stock.id)
            )
            await websocket.send_json(
                {
                    "type": "deltas",
                    "data": deltas,
                    "current_sequence_number": current_sequence_number,
                }
            )
            send_snapshot = False

    # Otherwise, send the initial snapshot to the client as a starting point.
    if send_snapshot:
        snapshot: dict[str, Any] | None = await order_book_cache.get_order_book(
            stock.id
        )
        if not snapshot:
            await websocket.close(code=1008, reason=f"No order book found for {ticker}")
            return
        current_sequence_number = (
            await order_book_cache.get_current_delta_sequence_number(stock.id)
        )
        await websocket.send_json(
            {
                "type": "snapshot",
                "data": snapshot,
                "current_sequence_number": current_sequence_number,
            }
        )

    # Subscribe the client to the ticker.
    ws_manager.subscribe(websocket, ticker)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await ws_manager.unsubscribe(websocket, ticker)
