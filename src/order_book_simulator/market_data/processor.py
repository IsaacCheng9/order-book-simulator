import logging
from typing import Any
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError

from order_book_simulator.database.connection import AsyncSessionLocal
from order_book_simulator.market_data.persistence import (
    persist_market_snapshot,
    persist_trades,
)

logger = logging.getLogger(__name__)


async def process_and_persist_market_data(stock_id: UUID, market_data: dict[str, Any]) -> None:
    """
    Processes and persists market data updates.

    Args:
        stock_id: The stock ID
        market_data: The market data update containing order book and trades
    """
    try:
        async with AsyncSessionLocal() as session:
            async with session.begin():
                # Persist order book snapshot
                await persist_market_snapshot(stock_id, market_data, session)
                logger.debug(
                    f"Persisted market snapshot for stock {stock_id} with "
                    f"{len(market_data['bids'])} bids and "
                    f"{len(market_data['asks'])} asks"
                )

                # Persist any new trades
                if trades := market_data.get("trades"):
                    await persist_trades(stock_id, trades, session)
                    logger.debug(f"Persisted {len(trades)} trades for stock {stock_id}")

                await session.commit()
    except SQLAlchemyError as e:
        logger.error(f"Failed to persist market data for stock {stock_id}: {e}")
        raise  # Re-raise to ensure the error is handled by the caller
