import asyncio
import json
import logging
import multiprocessing as mp
import time
from decimal import Decimal
from inspect import isawaitable
from typing import Awaitable, Callable

import aiohttp

from order_book_simulator.common.models import OrderRequest
from order_book_simulator.simulator.market_simulator_stats import MarketSimulatorStats
from order_book_simulator.simulator.random_order_generator import (
    RandomOrderGenerator,
)

logger = logging.getLogger(__name__)


class MarketSimulator:
    """
    Simulates market order flow with dynamic rate adjustment.

    This class generates and processes orders at an adjustable rate,
    automatically scaling the rate up or down based on system performance.

    Attributes:
        generator: The main order generator instance
        rate_increase_factor: Factor to multiply rate by when increasing
        rate_decrease_factor: Factor to multiply rate by when decreasing
        rate_adjustment_interval: Seconds between rate adjustments
        min_adjustment_wait: Minimum seconds between adjustments
        rate_increase_threshold: Achievement ratio needed to increase rate
        rate_decrease_threshold: Achievement ratio that triggers decrease
        stats_interval: Seconds between stats reports
        num_workers: Number of worker processes
        queue_size: Maximum size of the order queue
        num_producers: Number of parallel order producers
    """

    def __init__(
        self,
        tickers: list[str],
        base_prices: dict[str, float],
        min_order_sizes: dict[str, Decimal],
        max_order_sizes: dict[str, Decimal],
        initial_orders_per_second: int = 100,
        rate_mode: str = "variable",
        rate_increase_factor: float = 1.2,
        rate_decrease_factor: float = 0.9,
        rate_adjustment_interval: float = 5.0,
        min_adjustment_wait: float = 5.0,
        stats_interval: float = 1.0,
        num_workers: int = 5,
        queue_size: int = 1000,
        rate_increase_threshold: float = 0.95,
        rate_decrease_threshold: float = 0.8,
        num_producers: int = 8,
    ):
        """
        Initialises the market simulator.

        Args:
            tickers: list of stock tickers to simulate
            base_prices: Base prices for each ticker
            min_order_sizes: Minimum order size for each ticker
            max_order_sizes: Maximum order size for each ticker
            initial_orders_per_second: Starting order rate
            mode: "fixed" or "variable" rate mode
            rate_increase_factor: Factor to multiply rate by when increasing
            rate_decrease_factor: Factor to multiply rate by when decreasing
            rate_adjustment_interval: Seconds between rate adjustments
            min_adjustment_wait: Minimum seconds between adjustments
            stats_interval: Seconds between stats reports
            num_workers: Number of worker processes
            queue_size: Maximum size of the order queue
            rate_increase_threshold: Achievement ratio needed to increase rate
            rate_decrease_threshold: Achievement ratio that triggers decrease
            num_producers: Number of parallel order producers
        """
        self.rate_mode = rate_mode.lower()
        if self.rate_mode not in ["fixed", "variable"]:
            raise ValueError("Mode must be either 'fixed' or 'variable'")

        self.generator = RandomOrderGenerator(
            tickers=tickers,
            base_prices=base_prices,
            min_order_sizes=min_order_sizes,
            max_order_sizes=max_order_sizes,
            orders_per_second=initial_orders_per_second,
        )
        # Rate control parameters
        self.rate_increase_factor = rate_increase_factor
        self.rate_decrease_factor = rate_decrease_factor
        self.rate_adjustment_interval = rate_adjustment_interval
        self.min_adjustment_wait = min_adjustment_wait
        self.rate_increase_threshold = rate_increase_threshold
        self.rate_decrease_threshold = rate_decrease_threshold

        # Stats and reporting
        self.stats_interval = stats_interval
        self.stats = MarketSimulatorStats()
        self.start_time = mp.Value("d", 0.0)
        self.last_report_time = mp.Value("d", 0.0)
        self.last_adjustment_time = mp.Value("d", 0.0)

        # Concurrent processing
        self.num_workers = num_workers
        self.queue_size = queue_size
        self.order_queue = asyncio.Queue(maxsize=queue_size)
        self.workers = []
        self.running = False

        # Producer management
        self.num_producers = num_producers
        self.consecutive_increases = 0
        self.consecutive_decreases = 0
        self._setup_producers()

    def _setup_producers(self) -> None:
        """
        Sets up multiple order producers.

        Creates multiple RandomOrderGenerator instances to produce orders in
        parallel, dividing the total target rate among them.
        """
        self.producers = []
        orders_per_producer = self.generator.orders_per_second / self.num_producers

        for _ in range(self.num_producers):
            producer = RandomOrderGenerator(
                tickers=self.generator.tickers,
                base_prices=self.generator.base_prices,
                min_order_sizes=self.generator.min_order_sizes,
                max_order_sizes=self.generator.max_order_sizes,
                orders_per_second=orders_per_producer,
            )
            self.producers.append(producer)

    def _update_producer_rates(self, new_total_rate: int) -> None:
        """
        Updates the rate for all producers.

        Args:
            new_total_rate: The new total target rate to distribute among
                            producers
        """
        orders_per_producer = new_total_rate / len(self.producers)
        for producer in self.producers:
            producer.orders_per_second = orders_per_producer
        self.generator.orders_per_second = new_total_rate

    async def _adjust_rate(self) -> None:
        """
        Automatically adjusts the order rate based on performance.

        Monitors the actual achieved rate over a rolling window and adjusts the
        target rate up or down based on performance. Uses consecutive successes
        or failures to adjust the magnitude of changes.
        """
        if self.rate_mode == "fixed":
            return  # Don't adjust rate in fixed mode

        while self.running:
            await asyncio.sleep(self.rate_adjustment_interval)

            current_time = time.time()
            if (
                current_time - self.last_adjustment_time.value
                < self.min_adjustment_wait
            ):
                continue

            current_rate = self.stats.get_current_rate()
            target_rate = self.generator.orders_per_second
            rate_achievement = current_rate / target_rate if target_rate > 0 else 0

            # If we're achieving close to target rate, increase
            if rate_achievement > self.rate_increase_threshold:
                increase_factor = min(
                    2.0,
                    self.rate_increase_factor * (1 + self.consecutive_increases * 0.2),
                )
                new_rate = int(target_rate * increase_factor)
                logger.info(
                    f"Increasing rate from {target_rate} to {new_rate} orders/s "
                    f"(consecutive increases: {self.consecutive_increases})"
                )
                self._update_producer_rates(new_rate)
                self.consecutive_increases += 1
                self.consecutive_decreases = 0
            # If we're significantly below target, decrease
            elif rate_achievement < self.rate_decrease_threshold:
                decrease_factor = max(
                    0.5,
                    self.rate_decrease_factor * (1 - self.consecutive_decreases * 0.1),
                )
                new_rate = max(1, int(target_rate * decrease_factor))
                logger.info(
                    f"Decreasing rate from {target_rate} to {new_rate} orders/s "
                    f"(consecutive decreases: {self.consecutive_decreases})"
                )
                self._update_producer_rates(new_rate)
                self.consecutive_decreases += 1
                self.consecutive_increases = 0
            else:
                logger.info(
                    f"Rate {current_rate:.1f}/s at {rate_achievement:.1%} of target "
                    f"{target_rate:.1f}/s"
                )
                self.consecutive_increases = 0
                self.consecutive_decreases = 0

            self.last_adjustment_time.value = current_time

    async def _report_stats(self) -> None:
        """
        Reports order generation statistics periodically.

        Logs the current order rate, target rate, success rate, and total
        orders at regular intervals defined by stats_interval.
        """
        while self.running:
            await asyncio.sleep(self.stats_interval)
            current_rate = self.stats.get_current_rate()
            logger.info(
                f"Stats - Rate: {current_rate:.1f}/s "
                f"(target: {self.generator.orders_per_second:.1f}/s) | "
                f"Total Orders: {self.stats.total_orders}"
            )

    async def _order_producer(self) -> None:
        """
        Produces orders and puts them in the queue.

        Creates tasks for each producer to generate orders in parallel and puts
        them into the shared order queue. When finished, sends stop signals to
        all workers.
        """
        try:
            producer_tasks = [
                asyncio.create_task(self._run_producer(producer))
                for producer in self.producers
            ]
            await asyncio.gather(*producer_tasks)
        finally:
            for _ in range(self.num_workers):
                await self.order_queue.put(None)

    async def _run_producer(self, producer: RandomOrderGenerator) -> None:
        """
        Runs a single producer. Continuously generates orders from the producer
        and puts them in the queue until the simulator is stopped.

        Args:
            producer: The order generator instance to run
        """
        async for order in producer.generate_orders_async():
            if not self.running:
                break
            await self.order_queue.put(order)

    async def _order_worker(
        self,
        worker_id: int,
        order_handler: Callable[
            [OrderRequest, aiohttp.ClientSession | None], bool | Awaitable[bool]
        ],
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        """
        Worker that processes orders from the queue.

        Args:
            worker_id: Unique identifier for this worker
            order_handler: Function to process each order
            session: Optional HTTP session for making requests
        """
        while self.running:
            order = await self.order_queue.get()
            if order is None:  # Stop signal
                self.order_queue.task_done()
                break

            try:
                result = order_handler(order, session)
                success = await result if isawaitable(result) else result
                if success:
                    self.stats.record_order()
            except Exception as e:
                logger.error(f"Worker {worker_id} error processing order: {e}")
            finally:
                self.order_queue.task_done()

    def _set_up_http_client_settings(
        self,
    ) -> tuple[aiohttp.TCPConnector, aiohttp.ClientTimeout]:
        """
        Sets up HTTP client settings.

        Returns:
            A tuple containing the TCP connector and client timeout settings.
        """
        connector = aiohttp.TCPConnector(
            limit=0,
            limit_per_host=0,
            ttl_dns_cache=300,
            use_dns_cache=True,
            force_close=False,
        )

        timeout = aiohttp.ClientTimeout(
            total=30,
            connect=5,
            sock_connect=5,
            sock_read=10,
        )

        return connector, timeout

    async def _send_order_http(
        self, order: OrderRequest, session: aiohttp.ClientSession | None, api_url: str
    ) -> bool:
        """
        Sends a single order to the server via HTTP.

        Args:
            order: The order to send
            session: The HTTP session to use
            api_url: The URL of the API to send the order to

        Returns:
            True if the order was sent successfully, False otherwise.
        """
        if not session:
            return False
        try:
            async with session.post(
                f"{api_url.rstrip('/')}/v1/order-book",
                json={
                    "user_id": str(order.user_id),
                    "ticker": order.ticker,
                    "type": order.type.value,
                    "side": order.side.value,
                    "price": str(order.price) if order.price else None,
                    "quantity": str(order.quantity),
                    "time_in_force": order.time_in_force,
                    "client_order_id": order.client_order_id,
                },
            ) as response:
                return response.status == 200
        except Exception as e:
            logger.error(f"Error sending order: {e}")
            return False

    async def run_with_http(self, api_url: str) -> None:
        """
        Runs simulator sending orders via HTTP.

        Args:
            api_url: The URL of the API to send the order to
        """
        connector, timeout = self._set_up_http_client_settings()

        async with aiohttp.ClientSession(
            connector=connector,
            timeout=timeout,
            json_serialize=json.dumps,
            raise_for_status=True,
        ) as session:
            try:
                await self._check_server_health(api_url, session)
                await self.run(
                    lambda order, sess: self._send_order_http(order, sess, api_url),
                    session,
                )
            except Exception as e:
                logger.error(f"Failed to connect to server: {e}")

    async def _check_server_health(
        self, api_url: str, session: aiohttp.ClientSession
    ) -> None:
        """
        Checks if the server is healthy.

        Args:
            api_url: The URL of the API to check the health of
            session: The HTTP session to use
        """
        async with session.get(f"{api_url.rstrip('/')}/v1/health") as response:
            if response.status != 200:
                raise RuntimeError(f"Server health check failed: {response.status}")
            logger.info("Server health check passed, starting simulation")

    async def run(
        self,
        order_handler: Callable[
            [OrderRequest, aiohttp.ClientSession | None], bool | Awaitable[bool]
        ],
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        """
        Core simulation loop with custom order handling.

        Starts the stats reporting, rate adjustment, order production and order
        processing workers. Coordinates all the async tasks and ensures proper
        cleanup on exit.

        Args:
            order_handler: Function to process each order, can be sync or async
            session: Optional HTTP session for making requests
        """
        self.running = True
        with self.start_time.get_lock():
            self.start_time.value = time.time()
        with self.last_report_time.get_lock():
            self.last_report_time.value = self.start_time.value

        # Start stats and rate adjustment tasks
        stats_task = asyncio.create_task(self._report_stats())
        rate_task = asyncio.create_task(self._adjust_rate())

        try:
            # Start producer
            producer_task = asyncio.create_task(self._order_producer())

            # Start workers
            self.workers = [
                asyncio.create_task(self._order_worker(i, order_handler, session))
                for i in range(self.num_workers)
            ]

            # Wait for producer to finish
            await producer_task

            # Wait for all orders to be processed
            await self.order_queue.join()

            # Wait for workers to finish
            await asyncio.gather(*self.workers)

        finally:
            self.running = False
            await asyncio.gather(stats_task, rate_task)
