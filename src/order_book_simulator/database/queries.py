from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from order_book_simulator.database.db_models import Stock


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
