from argparse import Namespace
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from order_book_simulator.simulator import cli


@pytest.mark.asyncio
async def test_run_simulation_maps_cli_arguments_to_simulator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Map command-line arguments into the simulator configuration."""
    simulator = MagicMock()
    simulator.run_with_http = AsyncMock()
    simulator_class = MagicMock(return_value=simulator)
    monkeypatch.setattr(cli, "MarketSimulator", simulator_class)
    args = Namespace(
        api_url="http://gateway:8000",
        orders_per_second=250,
        rate_mode="fixed",
        workers=3,
        producers=4,
        queue_size=500,
    )

    await cli.run_simulation(args)

    simulator_class.assert_called_once_with(
        tickers=list(cli.DEFAULT_STOCK_PRICES),
        base_prices=cli.DEFAULT_STOCK_PRICES,
        min_order_sizes={ticker: Decimal(1) for ticker in cli.DEFAULT_STOCK_PRICES},
        max_order_sizes={ticker: Decimal(100) for ticker in cli.DEFAULT_STOCK_PRICES},
        initial_orders_per_second=250,
        rate_mode="fixed",
        num_workers=3,
        queue_size=500,
        num_producers=4,
    )
    simulator.run_with_http.assert_awaited_once_with("http://gateway:8000")


def test_main_exits_nonzero_when_simulation_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Return a failure status when the simulator cannot run."""
    monkeypatch.setattr(
        cli,
        "run_simulation",
        AsyncMock(side_effect=RuntimeError("gateway unavailable")),
    )

    with pytest.raises(SystemExit) as exc_info:
        cli.main([])

    assert exc_info.value.code == 1


@pytest.mark.parametrize("argument", ["--orders-per-second", "--workers"])
def test_parser_rejects_non_positive_counts(argument: str) -> None:
    """Reject non-positive concurrency and rate values."""
    parser = cli.build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args([argument, "0"])
