"""
Gets stock data in the database from NASDAQ and NYSE listings.

Sources:
https://www.nasdaq.com/market-activity/stocks/screener
https://github.com/datasets/nyse-other-listings/blob/main/data/nyse-listed.csv
"""

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
    def __init__(
        self,
        nasdaq_listings_path: str = "/app/resources/nasdaq_stocks_2025_02_11.csv",
        nyse_listings_path: str = "/app/resources/nyse_stocks_2025_02_12.csv",
    ):
        self.nasdaq_listings_path = Path(nasdaq_listings_path)
        self.nyse_listings_path = Path(nyse_listings_path)
        self.stocks: dict[str, dict[str, Any]] = {}  # ticker -> stock data

        if not self.nasdaq_listings_path.exists():
            raise FileNotFoundError(
                f"NASDAQ listings not found: {nasdaq_listings_path}. Current directory: {Path.cwd()}"
            )
        if not self.nyse_listings_path.exists():
            raise FileNotFoundError(
                f"NYSE listings not found: {nyse_listings_path}. Current directory: {Path.cwd()}"
            )

    def load_nasdaq_stocks(self) -> None:
        """Loads stock data from NASDAQ listings into self.stocks."""
        with open(self.nasdaq_listings_path, "r", encoding="utf-8") as file:
            reader = csv.DictReader(file)
            for row in reader:
                try:
                    # Get last sale price and remove $ symbol
                    last_sale = float(row["Last Sale"].replace("$", ""))
                    ticker = row["Symbol"]

                    # Determine price precision based on price level
                    if last_sale < 0.001:  # Ultra-low priced stocks (e.g. $0.0003)
                        price_precision = 8
                    elif last_sale < 0.01:  # Sub-penny stocks (e.g. $0.004)
                        price_precision = 6
                    else:  # Regular stocks (e.g. $1.23)
                        price_precision = 4

                    self.stocks[ticker] = {
                        "id": uuid.uuid4(),
                        "ticker": ticker,
                        "company_name": row["Name"],
                        "min_order_size": Decimal("1"),
                        "max_order_size": Decimal("1_000_000"),
                        "price_precision": price_precision,
                    }
                    logger.info(
                        f"Loaded NASDAQ data for {ticker} at ${last_sale:.6f} with precision {price_precision}"
                    )
                except (ValueError, KeyError) as e:
                    logger.error(f"Error processing NASDAQ row {row['Symbol']}: {e}")

        logger.info(f"Loaded {len(self.stocks)} stocks from NASDAQ")

    def load_nyse_stocks(self) -> None:
        """Loads additional stock data from NYSE listings into self.stocks."""
        with open(self.nyse_listings_path, "r", encoding="utf-8") as file:
            reader = csv.DictReader(file)
            initial_count = len(self.stocks)

            for row in reader:
                try:
                    ticker = row["ACT Symbol"]
                    if ticker in self.stocks:
                        logger.info(
                            f"Skipping NYSE stock {ticker} - already exists in NASDAQ"
                        )
                        continue

                    self.stocks[ticker] = {
                        "id": uuid.uuid4(),
                        "ticker": ticker,
                        # Some stocks have quotations at the start and end and
                        # some don't.
                        "company_name": row["Company Name"].replace('"', ""),
                        "min_order_size": Decimal("1"),
                        "max_order_size": Decimal("1_000_000"),
                        # NYSE stock listings CSV doesn't have last sale, so we
                        # have to assume a default precision.
                        "price_precision": 4,
                    }
                    logger.info(f"Loaded NYSE data for {ticker}")
                except (ValueError, KeyError) as e:
                    logger.error(
                        f"Error processing NYSE row {row.get('Symbol', 'UNKNOWN')}: {e}"
                    )

        new_stocks = len(self.stocks) - initial_count
        logger.info(f"Loaded {new_stocks} additional stocks from NYSE")

    async def insert_stocks_into_db(self) -> None:
        """Load all stocks and insert them into the database."""
        # Load stocks from both exchanges
        self.load_nasdaq_stocks()
        self.load_nyse_stocks()
        logger.info(f"Total of {len(self.stocks)} unique stocks to process")

        async with AsyncSessionLocal() as session:
            async with session.begin():
                try:
                    for stock in self.stocks.values():
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
