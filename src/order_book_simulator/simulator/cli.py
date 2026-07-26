import argparse
import asyncio
import logging
from collections.abc import Sequence
from decimal import Decimal

from order_book_simulator.simulator.market_simulator import MarketSimulator

DEFAULT_STOCK_PRICES = {
    "AAPL": 175.0,
    "MSFT": 380.0,
    "GOOGL": 140.0,
    "AMZN": 170.0,
    "META": 480.0,
    "NVDA": 790.0,
    "TSLA": 180.0,
}


def _positive_int(value: str) -> int:
    parsed_value = int(value)
    if parsed_value <= 0:
        raise argparse.ArgumentTypeError("value must be greater than zero")
    return parsed_value


def build_parser() -> argparse.ArgumentParser:
    """Build the market simulator command-line parser."""
    parser = argparse.ArgumentParser(
        description="Generate simulated orders for a running gateway",
    )
    parser.add_argument(
        "--api-url",
        default="http://localhost:8000",
        help="Gateway base URL (default: %(default)s)",
    )
    parser.add_argument(
        "--orders-per-second",
        type=_positive_int,
        default=100,
        help="Initial total order rate (default: %(default)s)",
    )
    parser.add_argument(
        "--rate-mode",
        choices=("fixed", "variable"),
        default="variable",
        help="Order-rate adjustment mode (default: %(default)s)",
    )
    parser.add_argument(
        "--workers",
        type=_positive_int,
        default=5,
        help="Concurrent order workers (default: %(default)s)",
    )
    parser.add_argument(
        "--producers",
        type=_positive_int,
        default=8,
        help="Concurrent order producers (default: %(default)s)",
    )
    parser.add_argument(
        "--queue-size",
        type=_positive_int,
        default=1000,
        help="Maximum queued orders (default: %(default)s)",
    )
    parser.add_argument(
        "--log-level",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        default="INFO",
        help="Logging level (default: %(default)s)",
    )
    return parser


async def run_simulation(args: argparse.Namespace) -> None:
    """Run the simulator using parsed command-line arguments."""
    tickers = list(DEFAULT_STOCK_PRICES)
    simulator = MarketSimulator(
        tickers=tickers,
        base_prices=DEFAULT_STOCK_PRICES,
        min_order_sizes={ticker: Decimal("1") for ticker in tickers},
        max_order_sizes={ticker: Decimal("100") for ticker in tickers},
        initial_orders_per_second=args.orders_per_second,
        rate_mode=args.rate_mode,
        num_workers=args.workers,
        queue_size=args.queue_size,
        num_producers=args.producers,
    )
    await simulator.run_with_http(args.api_url)


def main(argv: Sequence[str] | None = None) -> None:
    """Run the packaged market simulator command."""
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    try:
        asyncio.run(run_simulation(args))
    except KeyboardInterrupt:
        logging.getLogger(__name__).info("Simulation stopped by user")
    except Exception as exc:
        logging.getLogger(__name__).error("Simulation failed: %s", exc)
        raise SystemExit(1) from exc
