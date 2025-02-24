"""
An example showing how to use the market simulator with HTTP order submission
to randomly generate orders for the 'Magnificent Seven' stocks.
"""

import asyncio
import concurrent.futures
import logging
import multiprocessing as mp
from decimal import Decimal

import uvloop

from order_book_simulator.simulator.market_simulator import MarketSimulator

# Configure logging with timestamps
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logger = logging.getLogger(__name__)

# Randomly generate orders for the 'Magnificent Seven' stocks.
STOCK_CONFIG = {
    "AAPL": {"base_price": 175.0},  # Apple
    "MSFT": {"base_price": 380.0},  # Microsoft
    "GOOGL": {"base_price": 140.0},  # Alphabet
    "AMZN": {"base_price": 170.0},  # Amazon
    "META": {"base_price": 480.0},  # Meta
    "NVDA": {"base_price": 790.0},  # NVIDIA
    "TSLA": {"base_price": 180.0},  # Tesla
}


def get_optimal_process_count() -> int:
    """
    Gets optimal process count for I/O bound tasks.

    For I/O bound tasks like sending HTTP requests, we can use more workers
    than CPU cores since the workers spend most of their time waiting for
    responses.

    Returns:
        Number of worker processes to use
    """
    return max(mp.cpu_count() * 2, 4)  # At least 4 workers, usually more


async def run_simulation() -> None:
    """Runs the market simulator with the example configuration."""
    # Extract configuration
    tickers = list(STOCK_CONFIG.keys())
    base_prices = {ticker: cfg["base_price"] for ticker, cfg in STOCK_CONFIG.items()}
    min_sizes = {ticker: Decimal("1") for ticker in tickers}
    max_sizes = {ticker: Decimal("100") for ticker in tickers}

    # Create and run simulator
    simulator = MarketSimulator(
        rate_mode="fixed",
        initial_orders_per_second=1,
        tickers=tickers,
        base_prices=base_prices,
        min_order_sizes=min_sizes,
        max_order_sizes=max_sizes,
        num_workers=get_optimal_process_count(),
    )

    await simulator.run_with_http("http://localhost:8000")


def main() -> None:
    # Setup event loop with uvloop for better performance
    uvloop.install()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.set_default_executor(concurrent.futures.ThreadPoolExecutor(max_workers=20))

    # Run simulation
    try:
        asyncio.run(run_simulation())
    except KeyboardInterrupt:
        logger.info("Simulation stopped by user")
    except Exception as e:
        logger.error(f"Simulation failed: {e}", exc_info=True)


if __name__ == "__main__":
    main()
