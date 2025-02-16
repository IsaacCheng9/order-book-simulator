from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import uuid4

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


@order_book_router.post("", response_model=OrderResponse)
async def create_order(order_request: OrderRequest, db=Depends(get_db)):
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
    """Returns all order books in the system."""
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "order_books": order_book_cache.get_all_order_books(),
    }


@order_book_router.get("/{ticker}")
async def get_order_book(
    ticker: str, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    """Returns the order book for the specified stock."""
    # Look up stock_id from ticker
    stock = await get_stock_by_ticker(ticker, db)
    if not stock:
        raise HTTPException(
            status_code=404,
            detail=f"No stock found with ticker {ticker}",
        )

    snapshot = order_book_cache.get_order_book(stock.id)
    if not snapshot:
        raise HTTPException(
            status_code=404,
            detail=f"No order book found for {ticker}",
        )

    return {
        "ticker": ticker,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "book": snapshot,
    }
