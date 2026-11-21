from fastapi import APIRouter, WebSocket
from redis.exceptions import RedisError
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

    # Subscribe into a bootstrap buffer before reading Redis so deltas
    # published during snapshot/recovery reads are never missed.
    connection = ws_manager.subscribe(websocket, stock.ticker, bootstrap=True)
    try:
        send_snapshot = True
        current_sequence_number = 0
        if last_seen_sequence_number is not None:
            try:
                (
                    deltas,
                    current_sequence_number,
                ) = await order_book_cache.get_deltas_since_with_sequence(
                    stock.id, last_seen_sequence_number
                )
            except RedisError, OSError:
                if not connection.is_overflowed:
                    await websocket.close(
                        code=1011, reason="Temporary cache error; reconnect to resync"
                    )
                return
            if connection.is_overflowed:
                return
            if deltas is not None:
                await websocket.send_json(
                    {
                        "type": "deltas",
                        "data": deltas,
                        "current_sequence_number": current_sequence_number,
                    }
                )
                send_snapshot = False

        if send_snapshot:
            try:
                (
                    snapshot,
                    current_sequence_number,
                ) = await order_book_cache.get_order_book_with_sequence(stock.id)
            except RedisError, OSError:
                if not connection.is_overflowed:
                    await websocket.close(
                        code=1011, reason="Temporary cache error; reconnect to resync"
                    )
                return
            if connection.is_overflowed:
                return
            if snapshot is None:
                await websocket.close(
                    code=1008, reason=f"No order book found for {stock.ticker}"
                )
                return
            await websocket.send_json(
                {
                    "type": "snapshot",
                    "data": snapshot,
                    "current_sequence_number": current_sequence_number,
                }
            )

        if not ws_manager.complete_bootstrap(
            websocket, stock.ticker, current_sequence_number
        ):
            return

        while True:
            await websocket.receive_text()
    except WebSocketDisconnect, RuntimeError, OSError:
        pass
    finally:
        await ws_manager.unsubscribe(websocket, stock.ticker, connection=connection)
