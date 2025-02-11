from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from order_book_simulator.common.cache import order_book_cache
from order_book_simulator.database.queries import (
    get_stock_by_ticker,
    get_tickers_by_ids,
)
from order_book_simulator.database.session import get_db

router = APIRouter(tags=["market-data"])


@router.get("/order-book/{ticker}")
async def get_order_book(
    ticker: str, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    """Returns the current state of the order book for the specified stock."""
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


@router.get("/order-books")
async def get_all_order_books() -> dict[str, Any]:
    """Returns the current state of all order books."""
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "order_books": order_book_cache.get_all_order_books(),
    }


@router.get("/stocks")
async def get_active_stocks(db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """Returns a list of all stock tickers that have an active order book."""
    order_books = order_book_cache.get_all_order_books()
    # Convert UUIDs to tickers
    stock_ids = [UUID(id_) for id_ in order_books.keys()]
    tickers = await get_tickers_by_ids(stock_ids, db)
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "tickers": sorted(tickers),
    }
