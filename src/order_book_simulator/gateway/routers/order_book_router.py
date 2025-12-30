from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from order_book_simulator.common.cache import order_book_cache
from order_book_simulator.common.models import OrderRequest, OrderResponse, OrderStatus
from order_book_simulator.database.connection import get_db
from order_book_simulator.database.queries import (
    get_stock_by_ticker,
)
from order_book_simulator.gateway.app_state import app_state
from order_book_simulator.gateway.validation import validate_order

order_book_router = APIRouter()


@order_book_router.delete("/orders/{order_id}")
async def cancel_order(
    order_id: UUID,
    stock_id: UUID,
    ticker: str,
) -> dict[str, str]:
    """
    Cancels an existing order.

    Args:
        order_id: The ID of the order to cancel.
        stock_id: The ID of the stock to cancel the order for.
        ticker: The ticker symbol of the stock to cancel the order for.

    Returns:
        Confirmation of the cancelled order.
    """
    if app_state.producer is None:
        raise HTTPException(
            status_code=503, detail="Order processing service is unavailable"
        )
    await app_state.producer.cancel_order(order_id, stock_id, ticker)
    return {
        "status": "accepted",
        "order_id": str(order_id),
        "reason": "Order cancellation request submitted",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@order_book_router.post("", response_model=OrderResponse)
async def create_order(order_request: OrderRequest, db=Depends(get_db)):
    """
    Creates a new order for a stock.

    Args:
        order_request: The order request to create.

    Returns:
        Confirmation of the created order including any filled quantity.
    """
    start_time = datetime.now(timezone.utc)
    if app_state.producer is None:
        raise HTTPException(
            status_code=503, detail="Order processing service is unavailable"
        )

    async with db.begin():
        stock = await validate_order(order_request, db)

        order_record = {
            "id": uuid4(),
            "stock_id": stock.id,
            "ticker": stock.ticker,
            **order_request.model_dump(),
            "status": OrderStatus.PENDING,
            "filled_quantity": Decimal("0"),
            "total_fee": Decimal("0"),
            "gateway_received_at": start_time.isoformat(),
        }

        await app_state.producer.send_order(order_record)
        return OrderResponse(
            **order_record, created_at=start_time, updated_at=start_time
        )


@order_book_router.get("/collection")
async def get_order_books() -> dict[str, Any]:
    """
    Returns all order books and their trades in the system.

    Returns:
        A dictionary of order books keyed by stock ID.
    """
    order_books = await order_book_cache.get_all_order_books()
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "order_books": {
            stock_id: {
                "bids": book["bids"],
                "asks": book["asks"],
                "trades": book.get("trades", []),
            }
            for stock_id, book in order_books.items()
        },
    }


@order_book_router.get("/{ticker}")
async def get_order_book(
    ticker: str, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    """
    Returns the order book for the specified stock.

    Args:
        ticker: The ticker of the stock to get the order book for.
        db: The database session.

    Returns:
        The order book for the specified stock.
    """
    stock = await get_stock_by_ticker(ticker, db)
    if not stock:
        raise HTTPException(
            status_code=404,
            detail=f"No stock found with ticker {ticker}",
        )

    snapshot = await order_book_cache.get_order_book(stock.id)
    trades = await order_book_cache.get_trades(stock.id)

    if not snapshot:
        raise HTTPException(
            status_code=404,
            detail=f"No order book found for {ticker}",
        )

    return {
        "ticker": ticker,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "book": snapshot,
        "trades": trades,
    }
