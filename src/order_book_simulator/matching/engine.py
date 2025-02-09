from datetime import datetime, timezone
from typing import Any, Awaitable, Callable
from uuid import UUID

from order_book_simulator.common.cache import order_book_cache
from order_book_simulator.matching.order_book import OrderBook


class MatchingEngine:
    """
    Coordinates order processing across multiple instruments.
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
        self, instrument_id: UUID, order_book: OrderBook, trades: list[dict[str, Any]]
    ) -> None:
        """
        Publishes market data updates for an instrument.

        Args:
            instrument_id: The unique identifier for the instrument.
            order_book: The order book for the instrument.
            trades: The list of trades that triggered this update.
        """
        market_data = {
            "bids": [
                {"price": price, "quantity": level.quantity}
                for price, level in order_book.bids.items()
            ],
            "asks": [
                {"price": price, "quantity": level.quantity}
                for price, level in order_book.asks.items()
            ],
            "trades": trades,
        }
        await self.market_data_publisher(instrument_id, market_data)
        # Update Redis cache with latest snapshot
        order_book_cache.set_order_book(instrument_id, market_data)

    async def process_order(self, order_message: dict[str, Any]) -> None:
        """
        Processes an incoming order message.

        Args:
            order_message: The deserialised order message from Kafka.
        """
        instrument_id = UUID(order_message["instrument_id"])
        order_book = self.order_books.get(instrument_id)
        if not order_book:
            order_book = OrderBook(instrument_id)
            self.order_books[instrument_id] = order_book

        # Add timestamp of when the order was processed.
        order_message["created_at"] = datetime.now(timezone.utc)
        trades = order_book.add_order(order_message)
        # Always publish market data updates, even if no trades occurred.
        await self._publish_market_data(instrument_id, order_book, trades or [])
