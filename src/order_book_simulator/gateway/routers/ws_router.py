from fastapi import APIRouter, WebSocket

from order_book_simulator.common.cache import order_book_cache
from order_book_simulator.database.connection import AsyncSessionLocal
from order_book_simulator.database.queries import get_stock_by_ticker
from order_book_simulator.gateway.ws_manager import WebSocketConnectionManager

ws_router = APIRouter()
ws_connection_manager = WebSocketConnectionManager()


@ws_router.websocket("/{ticker}")
async def order_book_ws(websocket: WebSocket, ticker: str):
    """
    Handles a WebSocket connection for real-time order book updates.

    Validates the ticker, sends an initial snapshot with the current sequence
    number, then holds the connection open for delta broadcasts. Cleans up the
    subscription on disconnect.

    Args:
        websocket: The WebSocket connection.
        ticker: The ticker to subscribe to.
    """
    await websocket.accept()

    # Validate the ticker against the DB.
    async with AsyncSessionLocal() as db:
        stock = await get_stock_by_ticker(ticker, db)
    if not stock:
        await websocket.close(code=1008, reason=f"Ticker {ticker} not found")
        return

    # Send the initial snapshot to the client as a starting point.
    snapshot = await order_book_cache.get_order_book(stock.id)
    if not snapshot:
        await websocket.close(code=1008, reason=f"No order book found for {ticker}")
        return
    current_sequence_number = await order_book_cache.get_current_delta_sequence_number(
        stock.id
    )
    await websocket.send_json(
        {
            "type": "snapshot",
            "data": snapshot,
            "current_sequence_number": current_sequence_number,
        }
    )

    ws_connection_manager.subscribe(websocket, ticker)
    try:
        while True:
            await websocket.receive_text()
    finally:
        ws_connection_manager.unsubscribe(websocket, ticker)
