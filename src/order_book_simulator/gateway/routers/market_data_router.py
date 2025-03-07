from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from order_book_simulator.common.cache import order_book_cache
from order_book_simulator.database.connection import get_db
from order_book_simulator.database.queries import (
    get_stock_by_ticker,
    get_tickers_by_ids,
)
from order_book_simulator.market_data.analytics import MarketDataAnalytics

market_data_router = APIRouter()

# TODO: Move Redis client creation to a proper dependency
redis_client = Redis(host="redis", port=6379, decode_responses=True)
analytics = MarketDataAnalytics(redis_client)


@market_data_router.get("/stocks-with-orders")
async def get_active_stocks(db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """
    Returns a list of all stock tickers that have an active order book.

    Args:
        db: The database session.

    Returns:
        A dictionary containing the active stocks with their tickers.
    """
    order_books = order_book_cache.get_all_order_books()
    stock_ids = [UUID(id_) for id_ in order_books.keys()]
    tickers = await get_tickers_by_ids(stock_ids, db)
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "tickers": sorted(tickers),
    }


@market_data_router.get("/{ticker}")
async def get_market_data(
    ticker: str, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    """
    Returns current market data for a stock including:
    - Order book depth
    - Bid/ask spread
    - Mid price
    - Last trade

    Args:
        ticker: The ticker symbol of the stock to get market data for.
        db: The database session.

    Returns:
        A dictionary containing the market data for the given stock.
    """
    stock = await get_stock_by_ticker(ticker, db)
    if not stock:
        raise HTTPException(
            status_code=404, detail=f"Couldn't find stock with ticker {ticker}"
        )
    stock_id = stock.id

    depth_stats = await analytics.get_market_depth(stock_id)
    if not depth_stats:
        raise HTTPException(
            status_code=404, detail=f"No market data found for {ticker}"
        )

    vwap = await analytics.get_vwap(stock_id, window=timedelta(minutes=1))

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "ticker": ticker,
        "bid_depth": str(depth_stats["bid_depth"]),
        "ask_depth": str(depth_stats["ask_depth"]),
        "spread": str(depth_stats["spread"]) if depth_stats["spread"] else None,
        "mid_price": str(depth_stats["mid_price"])
        if depth_stats["mid_price"]
        else None,
        "vwap_1min": str(vwap) if vwap else None,
    }
