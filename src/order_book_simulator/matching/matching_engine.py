import orjson
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

from aiokafka import AIOKafkaProducer

from order_book_simulator.common.cache import order_book_cache
from order_book_simulator.market_data.analytics import MarketDataAnalytics
from order_book_simulator.matching.order_book import OrderBook


class MatchingEngine:
    """
    Coordinates order processing across multiple stocks.
    """

    def __init__(
        self,
        kafka_producer: AIOKafkaProducer,
        analytics: MarketDataAnalytics,
    ):
        """
        Creates a new matching engine to coordinate between order books and
        market data.

        Args:
            kafka_producer: The Kafka producer to use for publishing market
                            data.
            analytics: The analytics service to record market data.
        """
        self.order_books: dict[UUID, OrderBook] = {}
        self.producer = kafka_producer
        self.analytics = analytics

    async def _publish_market_data(
        self,
        stock_id: UUID,
        ticker: str,
        order_book: OrderBook,
        trades: list[dict[str, Any]],
    ) -> None:
        """
        Publishes market data updates for a stock.

        Args:
            stock_id: The unique identifier for the stock.
            ticker: The ticker symbol for the stock.
            order_book: The order book for the stock.
            trades: The list of trades that triggered this update.
        """
        # Serialise trades for Kafka and Redis.
        if trades:
            for trade in trades:
                if "timestamp" not in trade:
                    trade["timestamp"] = datetime.now(timezone.utc).isoformat()
                trade["price"] = str(trade["price"])
                trade["quantity"] = str(trade["quantity"])
                trade["buyer_order_id"] = str(trade["buyer_order_id"])
                trade["seller_order_id"] = str(trade["seller_order_id"])
                trade["stock_id"] = str(trade["stock_id"])
            await order_book_cache.append_trades(stock_id, trades)

        # Get snapshot for Kafka payload (already string-serialised).
        snapshot = order_book.get_full_snapshot()
        payload = {
            "stock_id": str(stock_id),
            "ticker": ticker,
            **snapshot,
            "trades": trades,
        }
        await self.producer.send_and_wait(
            "market-data",
            orjson.dumps(payload),
        )

        # Record analytics using the actual order book price levels (not the
        # serialised snapshot) so we have proper PriceLevel objects with
        # computed quantities.
        await self.analytics.record_state_from_order_book(
            stock_id=stock_id,
            bid_levels=list(order_book.bid_levels.values()),
            ask_levels=list(order_book.ask_levels.values()),
            last_trade_price=Decimal(trades[-1]["price"]) if trades else None,
            last_trade_quantity=Decimal(trades[-1]["quantity"]) if trades else None,
        )

    async def cancel_order(self, cancel_message: dict[str, Any]) -> dict[str, Any]:
        """
        Cancels an order from the order book.

        Args:
            cancel_message: Dictionary containing order_id and stock_id.

        Returns:
            Dictionary with cancellation result.
        """
        order_id = UUID(cancel_message["order_id"])
        stock_id = UUID(cancel_message["stock_id"])

        order_book = self.order_books.get(stock_id)
        if not order_book:
            return {"success": False, "reason": "Order book not found"}

        is_success = order_book.cancel_order(order_id)
        if not is_success:
            return {"success": False, "reason": "Order not found"}

        # Update the order book cache.
        await order_book_cache.set_order_book(stock_id, order_book.get_full_snapshot())
        # Publish market data update.
        await self._publish_market_data(
            stock_id,
            cancel_message["ticker"],
            order_book,
            [],  # No trades from the cancellation.
        )
        return {"success": True, "reason": "Order cancelled"}

    async def process_order(self, order_message: dict[str, Any]) -> None:
        """
        Processes an incoming order message.

        Args:
            order_message: The deserialised order message from Kafka.
        """
        stock_id = UUID(order_message["stock_id"])
        ticker = order_message["ticker"]
        order_book = self.order_books.get(stock_id)
        if not order_book:
            order_book = OrderBook(stock_id)
            self.order_books[stock_id] = order_book

        # Add timestamp of when the order was processed.
        order_message["created_at"] = datetime.now(timezone.utc)
        trades = order_book.add_order(order_message)

        # Cache the order book state
        await order_book_cache.set_order_book(stock_id, order_book.get_full_snapshot())

        # Always publish market data updates, even if no trades occurred.
        await self._publish_market_data(stock_id, ticker, order_book, trades or [])
