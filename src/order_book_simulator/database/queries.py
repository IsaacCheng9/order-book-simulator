from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from order_book_simulator.database.models import Stock


async def get_stock_by_ticker(ticker: str, db: AsyncSession) -> Stock | None:
    """Looks up a stock by its ticker symbol."""
    query = select(Stock).where(Stock.ticker == ticker)
    result = await db.execute(query)
    return result.scalar_one_or_none()


async def get_tickers_by_ids(stock_ids: list[UUID], db: AsyncSession) -> list[str]:
    """Gets ticker symbols for a list of stock IDs."""
    query = select(Stock.ticker).where(Stock.id.in_(stock_ids))
    result = await db.execute(query)
    return [row[0] for row in result]
