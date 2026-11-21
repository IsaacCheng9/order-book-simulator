from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import orjson
from aiokafka import AIOKafkaProducer

from order_book_simulator.common.cache import order_book_cache
from order_book_simulator.common.models import Delta
from order_book_simulator.market_data.analytics import MarketDataAnalytics
from order_book_simulator.matching.order_book import OrderBook
from order_book_simulator.multicast.multicast_publisher import MulticastPublisher


class MatchingEngine:
    """
    Coordinates order processing across multiple stocks.
    """

    def __init__(
        self,
        kafka_producer: AIOKafkaProducer,
        analytics: MarketDataAnalytics,
        multicast_publisher: MulticastPublisher | None = None,
    ):
        """
        Creates a new matching engine to coordinate between order books and
        market data.

        Args:
            kafka_producer: The Kafka producer to use for publishing market
                            data.
            analytics: The analytics service to record market data.
            multicast_publisher: The multicast publisher to use for publishing
                delta messages.
        """
        self.order_books: dict[UUID, OrderBook] = {}
        self.producer: AIOKafkaProducer = kafka_producer
        self.analytics: MarketDataAnalytics = analytics
        self.multicast_publisher: MulticastPublisher | None = multicast_publisher

    def _publish_deltas_to_multicast(self, deltas: list[Delta]) -> None:
        """
        Publishes the deltas to the multicast group.

        Args:
            deltas: The list of deltas to publish.
        """
        if self.multicast_publisher:
            for delta in deltas:
                self.multicast_publisher.send(
                    delta.ticker,
                    delta.sequence_number,
                    orjson.dumps(asdict(delta), default=str),
                )

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
                if "id" in trade:
                    trade["id"] = str(trade["id"])
                if "timestamp" not in trade:
                    trade["timestamp"] = datetime.now(UTC).isoformat()
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
            trades=trades,
            sequence_number=order_book.delta_buffer.current_sequence,
        )

    @staticmethod
    def _parse_event_time(raw_value: Any) -> datetime:
        """Resolve a deterministic UTC event timestamp from a Kafka message."""
        if isinstance(raw_value, datetime):
            return (
                raw_value
                if raw_value.tzinfo is not None
                else raw_value.replace(tzinfo=UTC)
            )
        if isinstance(raw_value, str):
            parsed = datetime.fromisoformat(raw_value)
            return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
        return datetime.now(UTC)

    async def sync_state_to_cache(self) -> None:
        """Sync rebuilt in-memory order books and delta buffers to Redis.

        Called once after startup Kafka replay completes so Redis holds the
        current snapshot, bounded delta history, and sequence watermark for
        every recovered stock, even if Redis started empty.
        """
        for stock_id, order_book in self.order_books.items():
            await order_book_cache.set_order_book(
                stock_id, order_book.get_full_snapshot()
            )
            await order_book_cache.replace_deltas(
                stock_id,
                order_book.delta_buffer.get_recent_deltas(),
                order_book.delta_buffer.current_sequence,
            )

    async def cancel_order(
        self,
        cancel_message: dict[str, Any],
        *,
        publish: bool = True,
    ) -> dict[str, Any]:
        """Cancel an order from the order book.

        Args:
            cancel_message: Dictionary containing order_id and stock_id.
            publish: Whether to publish downstream Redis, Kafka, analytics,
                and multicast side effects. Set to False during startup replay.

        Returns:
            Dictionary with cancellation result.
        """
        order_id = cancel_message["order_id"]
        stock_id = UUID(cancel_message["stock_id"])
        order_book = self.order_books.get(stock_id)
        if not order_book:
            return {"success": False, "reason": "Order book not found"}

        cancel_time = self._parse_event_time(cancel_message.get("cancelled_at"))
        is_success = order_book.cancel_order(
            order_id, timestamp=cancel_time.timestamp()
        )
        if not is_success:
            return {"success": False, "reason": "Order not found"}

        if not publish:
            return {"success": True, "reason": "Order cancelled"}

        # Update the order book cache. Redis Pub/Sub and UDP multicast remain
        # at-least-once on crash replay; clients deduplicate by sequence number.
        await order_book_cache.set_order_book(stock_id, order_book.get_full_snapshot())
        deltas = order_book.last_deltas
        if deltas:
            await order_book_cache.store_deltas(stock_id, deltas)
            await order_book_cache.publish_deltas(order_book.ticker, deltas)
            self._publish_deltas_to_multicast(deltas)

        # Publish market data update.
        await self._publish_market_data(
            stock_id,
            order_book.ticker,
            order_book,
            [],  # No trades from the cancellation.
        )
        return {"success": True, "reason": "Order cancelled"}

    async def process_order(
        self,
        order_message: dict[str, Any],
        *,
        publish: bool = True,
    ) -> None:
        """Process an incoming order message.

        Args:
            order_message: The deserialised order message from Kafka.
            publish: Whether to publish downstream Redis, Kafka, analytics,
                and multicast side effects. Set to False during startup replay.
        """
        stock_id = UUID(order_message["stock_id"])
        ticker = order_message["ticker"]
        order_book = self.order_books.get(stock_id)
        if not order_book:
            order_book = OrderBook(stock_id, ticker)
            self.order_books[stock_id] = order_book

        order_id = order_message["id"]
        normalised_id = order_id if isinstance(order_id, UUID) else UUID(str(order_id))
        if normalised_id in order_book.processed_order_ids:
            return

        # Derive deterministic processing time from the source record when
        # available so startup replay reproduces identical timestamps.
        event_time = self._parse_event_time(
            order_message.get("gateway_received_at") or order_message.get("created_at")
        )
        order_payload = {**order_message, "created_at": event_time}
        trades = order_book.add_order(order_payload)

        if not publish:
            return

        # Cache the order book state. Redis Pub/Sub and UDP multicast remain
        # at-least-once on crash replay; clients deduplicate by sequence number.
        await order_book_cache.set_order_book(stock_id, order_book.get_full_snapshot())
        deltas = order_book.last_deltas
        if deltas:
            await order_book_cache.store_deltas(stock_id, deltas)
            await order_book_cache.publish_deltas(ticker, deltas)
            self._publish_deltas_to_multicast(deltas)

        # Always publish market data updates, even if no trades occurred.
        await self._publish_market_data(stock_id, ticker, order_book, trades or [])
