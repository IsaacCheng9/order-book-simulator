"""Gets stock data in the database from NASDAQ CSV file."""

import asyncio
import csv
import logging
from decimal import Decimal
from pathlib import Path
from typing import Any
import uuid

from sqlalchemy import text

from order_book_simulator.database.connection import AsyncSessionLocal

logger = logging.getLogger(__name__)


class StockLoader:
    def __init__(self, csv_path: str = "/app/resources/nasdaq_stocks_2025_02_11.csv"):
        self.csv_path = Path(csv_path)
        if not self.csv_path.exists():
            raise FileNotFoundError(
                f"CSV file not found: {csv_path}. Current directory: {Path.cwd()}"
            )

    def get_stock_data(self) -> list[dict[str, Any]]:
        """
        Loads stock data from NASDAQ CSV file.

        Returns:
            A list of dictionaries containing stock data that matches the
            database schema for the Stock table.
        """
        stocks = []
        with open(self.csv_path, "r", encoding="utf-8") as file:
            reader = csv.DictReader(file)
            for row in reader:
                try:
                    # Get last sale price and remove $ symbol
                    last_sale = float(row["Last Sale"].replace("$", ""))

                    # Determine price precision based on price level
                    if last_sale < 0.001:  # Ultra-low priced stocks (e.g. $0.0003)
                        price_precision = 8
                    elif last_sale < 0.01:  # Sub-penny stocks (e.g. $0.004)
                        price_precision = 6
                    else:  # Regular stocks (e.g. $1.23)
                        price_precision = 4

                    stocks.append(
                        {
                            "id": uuid.uuid4(),
                            "ticker": row["Symbol"],
                            "company_name": row["Name"],
                            "min_order_size": Decimal("1"),
                            "max_order_size": Decimal("1_000_000"),
                            "price_precision": price_precision,
                        }
                    )
                    logger.info(
                        f"Loaded data for {row['Symbol']} at ${last_sale:.6f} with precision {price_precision}"
                    )
                except (ValueError, KeyError) as e:
                    logger.error(f"Error processing row {row['Symbol']}: {e}")

        return stocks

    async def insert_stocks_into_db(self) -> None:
        """Insert stocks into the database."""
        stocks = self.get_stock_data()
        logger.info(f"Loaded {len(stocks)} stocks from CSV")

        async with AsyncSessionLocal() as session:
            async with session.begin():
                try:
                    for stock in stocks:
                        try:
                            await session.execute(
                                text("""
                                INSERT INTO stock (
                                    id, ticker, company_name, min_order_size, 
                                    max_order_size, price_precision
                                ) VALUES (
                                    :id, :ticker, :company_name, :min_order_size,
                                    :max_order_size, :price_precision
                                ) ON CONFLICT (ticker) DO UPDATE SET
                                    company_name = EXCLUDED.company_name,
                                    min_order_size = EXCLUDED.min_order_size,
                                    max_order_size = EXCLUDED.max_order_size,
                                    price_precision = EXCLUDED.price_precision,
                                    updated_at = CURRENT_TIMESTAMP
                                """),
                                stock,
                            )
                            logger.info(f"Initialised/updated stock {stock['ticker']}")
                        except Exception as e:
                            logger.error(
                                f"Error processing stock {stock['ticker']}: {e}"
                            )
                            raise  # Re-raise to trigger rollback
                except Exception as e:
                    logger.error(f"Database transaction failed: {e}")
                    raise  # Session.begin() will handle rollback


async def get_all_stocks():
    """Get all stocks."""
    initialiser = StockLoader()
    await initialiser.insert_stocks_into_db()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(get_all_stocks())
