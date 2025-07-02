from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from order_book_simulator.common.cache import order_book_cache
from order_book_simulator.database.connection import get_db
from order_book_simulator.database.queries import (
    get_all_stocks,
    get_stock_by_ticker,
    get_stock_id_ticker_mapping,
)
from order_book_simulator.market_data.analytics import MarketDataAnalytics

market_data_router = APIRouter()

# TODO: Move Redis client creation to a proper dependency
redis_client = Redis(host="redis", port=6379, decode_responses=True)
analytics = MarketDataAnalytics(redis_client)


@market_data_router.get("/stocks-with-orders")
async def get_active_stocks(db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """
    Returns a mapping of stock IDs to tickers for all stocks with active order books.

    Args:
        db: The database session.

    Returns:
        A dictionary containing the stock_id -> ticker mapping and sorted tickers list.
    """
    order_books = order_book_cache.get_all_order_books()
    stock_ids = [UUID(id_) for id_ in order_books.keys()]

    # Get proper stock_id -> ticker mapping from database
    stock_id_to_ticker = await get_stock_id_ticker_mapping(stock_ids, db)
    tickers = list(stock_id_to_ticker.values())

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "tickers": sorted(tickers),
        "stock_id_to_ticker": stock_id_to_ticker,
    }


@market_data_router.get("/stocks")
async def get_all_stocks_endpoint(db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """
    Returns all stocks available in the database.

    Args:
        db: The database session.

    Returns:
        A dictionary containing all stocks with their tickers and company names.
    """
    stocks = await get_all_stocks(db)
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "stocks": stocks,
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
