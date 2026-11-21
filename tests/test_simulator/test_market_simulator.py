import asyncio
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
        min_order_sizes={"TEST": Decimal(1)},
        max_order_sizes={"TEST": Decimal(100)},
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


@pytest.mark.asyncio
async def test_run_cancellation_terminates_promptly_when_queue_is_full() -> None:
    """Cancel cleanly without deadlocking or leaking tasks when queue is full."""
    simulator = MarketSimulator(
        tickers=["TEST"],
        base_prices={"TEST": 100.0},
        min_order_sizes={"TEST": Decimal(1)},
        max_order_sizes={"TEST": Decimal(100)},
        initial_orders_per_second=1_000,
        rate_adjustment_interval=60.0,
        stats_interval=60.0,
        num_workers=2,
        num_producers=2,
        queue_size=2,
    )
    baseline_tasks = set(asyncio.all_tasks())
    workers_started = 0
    all_workers_started = asyncio.Event()
    block_forever = asyncio.Event()

    async def blocked_handler(order, session) -> bool:
        nonlocal workers_started
        workers_started += 1
        if workers_started >= simulator.num_workers:
            all_workers_started.set()
        await block_forever.wait()
        return True

    run_task = asyncio.create_task(simulator.run(blocked_handler))
    await asyncio.wait_for(all_workers_started.wait(), timeout=2.0)

    # Wait until the bounded queue fills while workers remain blocked.
    deadline = asyncio.get_running_loop().time() + 2.0
    while not simulator.order_queue.full():
        assert asyncio.get_running_loop().time() < deadline
        await asyncio.sleep(0.01)

    run_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(run_task, timeout=1.0)

    assert simulator.running is False
    assert len(simulator.workers) == 2
    assert all(worker.done() for worker in simulator.workers)
    assert set(asyncio.all_tasks()) == baseline_tasks


@pytest.mark.asyncio
async def test_stop_via_running_flag_terminates_without_deadlock() -> None:
    """Clearing running=False exits cleanly without hanging on join or workers."""
    simulator = MarketSimulator(
        tickers=["TEST"],
        base_prices={"TEST": 100.0},
        min_order_sizes={"TEST": Decimal(1)},
        max_order_sizes={"TEST": Decimal(100)},
        initial_orders_per_second=1_000,
        rate_adjustment_interval=60.0,
        stats_interval=60.0,
        num_workers=2,
        num_producers=2,
        queue_size=2,
    )
    baseline_tasks = set(asyncio.all_tasks())
    worker_started = asyncio.Event()
    block_forever = asyncio.Event()

    async def blocked_handler(order, session) -> bool:
        worker_started.set()
        await block_forever.wait()
        return True

    run_task = asyncio.create_task(simulator.run(blocked_handler))
    await asyncio.wait_for(worker_started.wait(), timeout=2.0)

    # Clearing running stops producers once they finish putting or break.
    simulator.running = False
    # Drain one queued item if producers are already blocked in put() so they
    # observe running=False and return normally into the else branch.
    while not simulator.order_queue.empty():
        simulator.order_queue.get_nowait()
        simulator.order_queue.task_done()

    await asyncio.wait_for(run_task, timeout=2.0)

    assert simulator.running is False
    assert len(simulator.workers) == 2
    assert all(worker.done() for worker in simulator.workers)
    assert set(asyncio.all_tasks()) == baseline_tasks


@pytest.mark.asyncio
async def test_producer_exception_cancels_sibling_producers_without_leaks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Propagate producer failure and cancel sibling producer tasks cleanly."""
    simulator = MarketSimulator(
        tickers=["TEST"],
        base_prices={"TEST": 100.0},
        min_order_sizes={"TEST": Decimal(1)},
        max_order_sizes={"TEST": Decimal(100)},
        initial_orders_per_second=100,
        rate_adjustment_interval=60.0,
        stats_interval=60.0,
        num_workers=1,
        num_producers=2,
        queue_size=2,
    )
    baseline_tasks = set(asyncio.all_tasks())
    first_producer = simulator.producers[0]
    block_forever = asyncio.Event()

    async def failing_run_producer(producer) -> None:
        if producer is first_producer:
            raise RuntimeError("producer exploded")
        await block_forever.wait()

    monkeypatch.setattr(simulator, "_run_producer", failing_run_producer)

    with pytest.raises(RuntimeError, match="producer exploded"):
        await asyncio.wait_for(
            simulator.run(AsyncMock(return_value=True)),
            timeout=2.0,
        )

    assert simulator.running is False
    assert set(asyncio.all_tasks()) == baseline_tasks
