from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from order_book_simulator.simulator import market_simulator as simulator_module
from order_book_simulator.simulator.market_simulator import MarketSimulator


@pytest.mark.asyncio
async def test_run_with_http_propagates_connection_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Propagate startup failures so the CLI can return a failure status."""
    simulator = MarketSimulator(
        tickers=["TEST"],
        base_prices={"TEST": 100.0},
        min_order_sizes={"TEST": Decimal("1")},
        max_order_sizes={"TEST": Decimal("100")},
        num_workers=1,
        num_producers=1,
    )
    session = MagicMock()
    session_context = MagicMock()
    session_context.__aenter__ = AsyncMock(return_value=session)
    session_context.__aexit__ = AsyncMock(return_value=None)
    monkeypatch.setattr(
        simulator,
        "_set_up_http_client_settings",
        MagicMock(return_value=(MagicMock(), MagicMock())),
    )
    monkeypatch.setattr(
        simulator_module.aiohttp,
        "ClientSession",
        MagicMock(return_value=session_context),
    )
    monkeypatch.setattr(
        simulator,
        "_check_server_health",
        AsyncMock(side_effect=RuntimeError("gateway unavailable")),
    )

    with pytest.raises(RuntimeError, match="gateway unavailable"):
        await simulator.run_with_http("http://gateway:8000")
