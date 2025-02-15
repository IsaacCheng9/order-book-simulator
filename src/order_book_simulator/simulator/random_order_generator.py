import asyncio
import random
import time
from decimal import Decimal
from typing import AsyncIterator, Iterator
from uuid import uuid4

from order_book_simulator.common.models import OrderRequest, OrderSide, OrderType


class RandomOrderGenerator:
    """
    Generates random orders with configurable parameters and adjusts the prices
    over time with a random walk to simulate more realistic price movements.
    """

    def __init__(
        self,
        tickers: list[str],
        base_prices: dict[str, float],
        # Set the minimum and maximum order sizes for each ticker.
        min_order_sizes: dict[str, Decimal],
        max_order_sizes: dict[str, Decimal],
        orders_per_second: float = 100.0,
        price_volatility: float = 0.02,  # 2% price volatility
        market_order_ratio: float = 0.3,  # 30% market orders by default
    ):
        self.tickers = tickers
        self.base_prices = base_prices
        self.min_order_sizes = min_order_sizes
        self.max_order_sizes = max_order_sizes
        self._orders_per_second = orders_per_second
        self._sleep_time = 1.0 / orders_per_second
        self.price_volatility = price_volatility
        self.market_order_ratio = market_order_ratio
        self._order_counter = 0
        self._last_prices = {ticker: price for ticker, price in base_prices.items()}
        self._client_id = str(uuid4())  # Add unique client ID per generator instance

    @property
    def orders_per_second(self) -> float:
        return self._orders_per_second

    @orders_per_second.setter
    def orders_per_second(self, value: float) -> None:
        self._orders_per_second = value
        self._sleep_time = 1.0 / value

    def _generate_order_id(self) -> str:
        """
        Generates a unique client order ID for this generator instance.
        Each client (generator) maintains its own sequence of order IDs.

        Returns:
            A unique client order ID in the format CLIENT_UUID_ORDER_NUM
        """
        self._order_counter += 1
        return f"{self._client_id}_{self._order_counter}"

    def _generate_quantity(self, ticker: str) -> Decimal:
        """
        Generates a valid quantity for the given ticker.

        Args:
            ticker: The ticker to generate a quantity for.

        Returns:
            A valid quantity for the given ticker.
        """
        min_size = self.min_order_sizes[ticker]
        max_size = self.max_order_sizes[ticker]
        # Share quantities must be whole numbers.
        quantity = random.randint(int(min_size), int(max_size))

        return Decimal(str(quantity))

    def _generate_next_price(self, ticker: str) -> Decimal:
        """
        Generates the next price with a random walk to simulate more realistic
        price movements.

        Args:
            ticker: The ticker to generate a price for.

        Returns:
            The next price for the given ticker.
        """
        current_price = self._last_prices[ticker]
        # Random walk with mean reversion
        price_change = random.gauss(0, self.price_volatility) * current_price
        new_price = current_price + price_change

        # Ensure price stays within reasonable bounds (±20% of base price)
        base_price = self.base_prices[ticker]
        min_price = base_price * 0.8
        max_price = base_price * 1.2
        new_price = max(min(new_price, max_price), min_price)
        self._last_prices[ticker] = new_price

        # Round to 2 decimal places for US equities
        return Decimal(str(new_price)).quantize(Decimal("0.01"))

    def _generate_single_order_randomly(self) -> OrderRequest:
        """
        Generates a single order randomly based on the parameters of the order
        generator.

        Returns:
            An Order object.
        """
        ticker = random.choice(self.tickers)
        side = random.choice([OrderSide.BUY, OrderSide.SELL])
        order_type = random.choices(
            [OrderType.MARKET, OrderType.LIMIT],
            weights=[self.market_order_ratio, 1 - self.market_order_ratio],
        )[0]

        # Generate price for limit orders only
        price = (
            None
            if order_type == OrderType.MARKET
            else self._generate_next_price(ticker)
        )
        quantity = self._generate_quantity(ticker)

        return OrderRequest(
            user_id=uuid4(),
            ticker=ticker,
            type=order_type,
            side=side,
            price=price,
            quantity=quantity,
            time_in_force="DAY",  # Default to day orders
            client_order_id=self._generate_order_id(),
        )

    async def generate_orders_async(self) -> AsyncIterator[OrderRequest]:
        """
        Asynchronously generates orders at the specified rate.

        Yields:
            A randomly generated order every 1/ orders_per_second seconds.
        """
        while True:
            yield self._generate_single_order_randomly()
            await asyncio.sleep(self._sleep_time)

    def generate_orders(self) -> Iterator[OrderRequest]:
        """
        Generates orders at the specified rate.

        Yields:
            A randomly generated order every 1/ orders_per_second seconds.
        """
        sleep_time = 1.0 / self.orders_per_second

        while True:
            yield self._generate_single_order_randomly()
            time.sleep(sleep_time)
