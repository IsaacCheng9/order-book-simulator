# src/order_book_simulator/market_data/publisher.py
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Awaitable, Callable, List, Optional
from uuid import UUID

from order_book_simulator.common.models import OrderBookState, PriceLevel
from order_book_simulator.market_data.analytics import MarketDataAnalytics
from order_book_simulator.market_data.processor import process_and_persist_market_data

logger = logging.getLogger(__name__)


class MarketDataPublisher:
    """
    Publishes market data updates to various destinations. Acts as a facade for
    different publishing mechanisms.
    """

    def __init__(
        self,
        analytics: MarketDataAnalytics,
        persistence_callback: Optional[Callable[[UUID, dict], Awaitable[None]]] = None,
    ):
        """
        Initialises the publisher with required dependencies.

        Args:
            analytics: The analytics service to record market data
            persistence_callback: Optional callback to persist market data. If
                                  None, process_and_persist_market_data will be
                                  used.
        """
        self.analytics = analytics
        self.persistence_callback = (
            persistence_callback or process_and_persist_market_data
        )
        self.additional_handlers: List[Callable[[UUID, dict], Awaitable[None]]] = []

    def _convert_to_order_book_state(
        self, stock_id: UUID, ticker: str, market_data: dict[str, Any]
    ) -> OrderBookState:
        """
        Converts market data to OrderBookState for analytics.

        Args:
            stock_id: The ID of the stock
            ticker: The stock ticker symbol
            market_data: The market data containing bids, asks, and trades

        Returns:
            An OrderBookState object representing the current state of the
            order book.
        """
        return OrderBookState(
            stock_id=stock_id,
            ticker=ticker,
            bids=[
                PriceLevel(
                    price=Decimal(bid["price"]),
                    quantity=Decimal(bid["quantity"]),
                    order_count=bid.get("order_count", 0),
                )
                for bid in market_data["bids"]
            ],
            asks=[
                PriceLevel(
                    price=Decimal(ask["price"]),
                    quantity=Decimal(ask["quantity"]),
                    order_count=ask.get("order_count", 0),
                )
                for ask in market_data["asks"]
            ],
            last_trade_price=Decimal(market_data["trades"][-1]["price"])
            if market_data["trades"]
            else None,
            last_trade_quantity=Decimal(market_data["trades"][-1]["quantity"])
            if market_data["trades"]
            else None,
            last_update_time=datetime.now(timezone.utc),
        )

    async def publish_market_data(
        self, stock_id: UUID, market_data: dict[str, Any]
    ) -> None:
        """
        Publishes market data updates to all configured destinations.

        Args:
            stock_id: The ID of the stock
            market_data: The market data to publish
        """
        logger.info(f"Publishing market data for {stock_id}")

        # Get the ticker from the message.
        ticker = market_data.get("ticker")
        if not ticker:
            logger.error(f"No ticker found in market data: {market_data}")
            return

        # Convert to OrderBookState for analytics
        state = self._convert_to_order_book_state(stock_id, ticker, market_data)
        # Record in analytics
        if state:
            await self.analytics.record_state(state)
        # Persist to database
        await self.persistence_callback(stock_id, market_data)

        # Call any additional handlers
        for handler in self.additional_handlers:
            try:
                await handler(stock_id, market_data)
            except Exception as e:
                logger.error(f"Error in market data handler: {e}")

    def add_handler(self, handler: Callable[[UUID, dict], Awaitable[None]]) -> None:
        """
        Adds an additional handler for market data.

        Args:
            handler: A callable that takes a stock_id and market_data dict and
                     returns an awaitable
        """
        self.additional_handlers.append(handler)
