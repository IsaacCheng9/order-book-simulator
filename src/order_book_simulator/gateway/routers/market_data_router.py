from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from order_book_simulator.common.cache import order_book_cache
from order_book_simulator.database.connection import get_db
from order_book_simulator.database.queries import (
    get_tickers_by_ids,
)

market_data_router = APIRouter()


@market_data_router.get("/stocks")
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
