from datetime import datetime
from uuid import UUID

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from order_book_simulator.database.db_models import Stock, Trade


async def get_stock_by_ticker(ticker: str, db: AsyncSession) -> Stock | None:
    """
    Gets a stock by its ticker symbol.

    Args:
        ticker: The ticker symbol to search for.
        db: The database session.

    Returns:
        The stock with the given ticker symbol, or None if no stock is found.
    """
    result = await db.execute(select(Stock).where(Stock.ticker == ticker.upper()))
    return result.scalar_one_or_none()


async def get_tickers_by_ids(stock_ids: list[UUID], db: AsyncSession) -> list[str]:
    """
    Gets ticker symbols for a list of stock IDs.

    Args:
        stock_ids: The list of stock IDs to get tickers for.
        db: The database session.

    Returns:
        A list of ticker symbols for the given stock IDs.
    """
    query = select(Stock.ticker).where(Stock.id.in_(stock_ids))
    result = await db.execute(query)
    return [row[0] for row in result]


async def get_stock_by_id(stock_id: UUID, db: AsyncSession) -> Stock | None:
    """
    Gets a stock by its ID.

    Args:
        stock_id: The ID of the stock to get.
        db: The database session.

    Returns:
        The stock with the given ID, or None if no stock is found.
    """
    result = await db.execute(select(Stock).where(Stock.id == stock_id))
    return result.scalar_one_or_none()


async def get_stock_id_ticker_mapping(
    stock_ids: list[UUID], db: AsyncSession
) -> dict[str, str]:
    """
    Gets a mapping of stock IDs to ticker symbols.

    Args:
        stock_ids: The list of stock IDs to get tickers for.
        db: The database session.

    Returns:
        A dictionary mapping stock ID strings to ticker symbols.
    """
    query = select(Stock.id, Stock.ticker).where(Stock.id.in_(stock_ids))
    result = await db.execute(query)
    return {str(row[0]): row[1] for row in result}


async def get_all_stocks(db: AsyncSession) -> list[dict[str, str]]:
    """
    Gets all stocks from the database.

    Args:
        db: The database session.

    Returns:
        A list of dictionaries containing stock information.
    """
    query = select(Stock.ticker, Stock.company_name).order_by(Stock.ticker)
    result = await db.execute(query)
    return [{"ticker": row[0], "company_name": row[1]} for row in result]


async def get_trades_by_stock(
    stock_id: UUID, db: AsyncSession, limit: int = 100
) -> list[dict]:
    """
    Gets recent trades for a specific stock.

    Args:
        stock_id: The stock ID to get trades for.
        db: The database session.
        limit: Maximum number of trades to return.

    Returns:
        A list of trade dictionaries.
    """
    query = (
        select(Trade)
        .where(Trade.stock_id == stock_id)
        .order_by(desc(Trade.trade_time))
        .limit(limit)
    )
    result = await db.execute(query)
    trades = result.scalars().all()

    return [
        {
            "id": str(trade.id),
            "stock_id": str(trade.stock_id),
            "price": str(trade.price),
            "quantity": str(trade.quantity),
            "total_amount": str(trade.total_amount),
            "trade_time": trade.trade_time.isoformat(),
            "buyer_order_id": str(trade.buyer_order_id),
            "seller_order_id": str(trade.seller_order_id),
        }
        for trade in trades
    ]


async def get_recent_trades(db: AsyncSession, limit: int = 100) -> list[dict]:
    """
    Gets recent trades across all stocks.

    Args:
        db: The database session.
        limit: Maximum number of trades to return.

    Returns:
        A list of trade dictionaries with stock information.
    """
    query = (
        select(Trade, Stock.ticker)
        .join(Stock, Trade.stock_id == Stock.id)
        .order_by(desc(Trade.trade_time))
        .limit(limit)
    )
    result = await db.execute(query)
    rows = result.all()

    return [
        {
            "id": str(trade.id),
            "stock_id": str(trade.stock_id),
            "ticker": ticker,
            "price": str(trade.price),
            "quantity": str(trade.quantity),
            "total_amount": str(trade.total_amount),
            "trade_time": trade.trade_time.isoformat(),
            "buyer_order_id": str(trade.buyer_order_id),
            "seller_order_id": str(trade.seller_order_id),
        }
        for trade, ticker in rows
    ]


async def get_trade_analytics_by_stock(
    stock_id: UUID, db: AsyncSession, since: datetime | None = None
) -> dict:
    """
    Gets trade analytics for a specific stock.

    Args:
        stock_id: The stock ID to get analytics for.
        db: The database session.
        since: Optional datetime to filter trades from.

    Returns:
        A dictionary containing trade analytics.

    Raises:
        ValueError: If no trades are found for the stock.
    """

    query = select(
        func.count(Trade.id).label("trade_count"),
        func.sum(Trade.quantity).label("total_volume"),
        func.sum(Trade.total_amount).label("total_value"),
        func.avg(Trade.price).label("avg_price"),
        func.min(Trade.price).label("min_price"),
        func.max(Trade.price).label("max_price"),
    ).where(Trade.stock_id == stock_id)

    if since:
        query = query.where(Trade.trade_time >= since)

    result = await db.execute(query)
    row = result.first()

    if row is None or row.trade_count == 0:
        raise ValueError(f"No trades found for stock {stock_id}")

    return {
        "trade_count": row.trade_count,
        "total_volume": str(row.total_volume or 0),
        "total_value": str(row.total_value or 0),
        "avg_price": str(row.avg_price) if row.avg_price is not None else None,
        "min_price": str(row.min_price) if row.min_price is not None else None,
        "max_price": str(row.max_price) if row.max_price is not None else None,
    }


async def get_global_trade_analytics(
    db: AsyncSession, since: datetime | None = None
) -> dict:
    """
    Gets trade analytics across all stocks.

    Args:
        db: The database session.
        since: Optional datetime to filter trades from.

    Returns:
        A dictionary containing global trade analytics.
    """

    query = select(
        func.count(Trade.id).label("trade_count"),
        func.sum(Trade.quantity).label("total_volume"),
        func.sum(Trade.total_amount).label("total_value"),
        func.avg(Trade.quantity).label("avg_quantity"),
        func.avg(Trade.price).label("avg_price"),
        func.min(Trade.price).label("min_price"),
        func.max(Trade.price).label("max_price"),
    )

    if since:
        query = query.where(Trade.trade_time >= since)

    result = await db.execute(query)
    row = result.first()

    if row is None or row.trade_count == 0:
        return {
            "trade_count": 0,
            "total_volume": "0",
            "total_value": "0",
            "avg_quantity": None,
            "avg_price": None,
            "min_price": None,
            "max_price": None,
        }

    return {
        "trade_count": row.trade_count,
        "total_volume": str(row.total_volume or 0),
        "total_value": str(row.total_value or 0),
        "avg_quantity": str(row.avg_quantity) if row.avg_quantity is not None else None,
        "avg_price": str(row.avg_price) if row.avg_price is not None else None,
        "min_price": str(row.min_price) if row.min_price is not None else None,
        "max_price": str(row.max_price) if row.max_price is not None else None,
    }
