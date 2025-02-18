from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Awaitable, Callable
from uuid import UUID

from order_book_simulator.common.cache import order_book_cache
from order_book_simulator.matching.order_book import OrderBook


class MatchingEngine:
    """
    Coordinates order processing across multiple stocks.
    """

    def __init__(self, market_data_publisher: Callable[[UUID, dict], Awaitable[None]]):
        """
        Creates a new matching engine to coordinate between order books and
        market data.

        Args:
            market_data_publisher: A callback to publish market data updates.
        """
        self.order_books: dict[UUID, OrderBook] = {}
        self.market_data_publisher = market_data_publisher

    async def _publish_market_data(
        self, stock_id: UUID, order_book: OrderBook, trades: list[dict[str, Any]]
    ) -> None:
        """
        Publishes market data updates for a stock.

        Args:
            stock_id: The unique identifier for the stock.
            order_book: The order book for the stock.
            trades: The list of trades that triggered this update.
        """
        # Get current order book state
        market_data = order_book.get_full_snapshot()

        # Convert Decimal objects to strings in trades
        if trades:
            for trade in trades:
                if "timestamp" not in trade:
                    trade["timestamp"] = datetime.now(timezone.utc).isoformat()
                # Convert Decimal values to strings
                trade["price"] = str(trade["price"])
                trade["quantity"] = str(trade["quantity"])

            existing_trades = order_book_cache.get_trades(stock_id)
            updated_trades = existing_trades + [t for t in trades if t is not None]
            order_book_cache.set_trades(stock_id, updated_trades)

        # Convert Decimal values in bids/asks to strings if not already done
        for level in market_data["bids"] + market_data["asks"]:
            if isinstance(level["price"], Decimal):
                level["price"] = str(level["price"])
            if isinstance(level["quantity"], Decimal):
                level["quantity"] = str(level["quantity"])

        await self.market_data_publisher(stock_id, {**market_data, "trades": trades})

    async def process_order(self, order_message: dict[str, Any]) -> None:
        """
        Processes an incoming order message.

        Args:
            order_message: The deserialised order message from Kafka.
        """
        stock_id = UUID(order_message["stock_id"])
        order_book = self.order_books.get(stock_id)
        if not order_book:
            order_book = OrderBook(stock_id)
            self.order_books[stock_id] = order_book

        # Add timestamp of when the order was processed.
        order_message["created_at"] = datetime.now(timezone.utc)
        trades = order_book.add_order(order_message)
        # Always publish market data updates, even if no trades occurred.
        await self._publish_market_data(stock_id, order_book, trades or [])
