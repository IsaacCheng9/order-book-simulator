from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, AsyncGenerator, cast
from uuid import UUID

import polars as pl
from redis.asyncio import Redis
from redis.typing import EncodableT, FieldT

from order_book_simulator.common.models import OrderBookState, PriceLevel


class MarketDataAnalytics:
    """
    Provides real-time market data analytics using Redis Streams and Polars.

    This class handles the storage and analysis of market data using Redis
    Streams for persistence and real-time access, with Polars for efficient
    analytics processing.

    Attributes:
        redis: Redis client instance for stream operations
        _window_size: Number of entries to maintain in each stream
    """

    def __init__(self, redis_client: Redis) -> None:
        """
        Creates a new analytics instance.

        Args:
            redis_client: Redis client for storing analytics data
        """
        self.redis = redis_client
        self._window_size = 1000  # Keep last 1000 entries

    def _get_stream_key(self, stock_id: UUID) -> str:
        """
        Gets the Redis stream key for a stock.

        Args:
            stock_id: Unique identifier of the stock

        Returns:
            Redis key string for the stock's market data stream
        """
        return f"market_data:{stock_id}"

    async def record_state(self, state: OrderBookState) -> None:
        """
        Records an order book state to Redis Stream for real-time analysis.

        This method calculates market metrics like mid price and spread, then
        stores them in a Redis Stream for later analysis.

        Args:
            state: Current state of the order book including bids, asks and
                   trades
        """
        best_bid = max(state.bids, key=lambda x: x.price).price if state.bids else None
        best_ask = min(state.asks, key=lambda x: x.price).price if state.asks else None

        market_data: dict[str, Any] = {
            "timestamp": state.last_update_time.isoformat(),
            "mid_price": str((best_bid + best_ask) / 2)
            if (best_bid and best_ask)
            else "",
            "spread": str(best_ask - best_bid) if (best_bid and best_ask) else "",
            "bid_depth": str(sum(level.quantity for level in state.bids)),
            "ask_depth": str(sum(level.quantity for level in state.asks)),
            "last_trade_price": str(state.last_trade_price)
            if state.last_trade_price
            else "",
            "last_trade_quantity": str(state.last_trade_quantity)
            if state.last_trade_quantity
            else "",
        }

        stream_key = self._get_stream_key(state.stock_id)
        await self.redis.xadd(
            stream_key,
            cast(dict[FieldT, EncodableT], market_data),
            maxlen=self._window_size,
            approximate=True,
        )

    async def record_state_from_order_book(
        self,
        stock_id: UUID,
        bid_levels: list[PriceLevel],
        ask_levels: list[PriceLevel],
        last_trade_price: Decimal | None,
        last_trade_quantity: Decimal | None,
    ) -> None:
        """
        Records order book state to Redis Stream using actual PriceLevels.

        This is more efficient than record_state() because it takes the actual
        PriceLevel objects from the order book, avoiding the need to
        reconstruct them from serialised data.

        Args:
            stock_id: Unique identifier of the stock.
            bid_levels: List of bid price levels from the order book.
            ask_levels: List of ask price levels from the order book.
            last_trade_price: Price of the last trade, if any.
            last_trade_quantity: Quantity of the last trade, if any.
        """
        best_bid = max(bid_levels, key=lambda x: x.price).price if bid_levels else None
        best_ask = min(ask_levels, key=lambda x: x.price).price if ask_levels else None

        now = datetime.now(timezone.utc)
        market_data: dict[str, Any] = {
            "timestamp": now.isoformat(),
            "mid_price": str((best_bid + best_ask) / 2)
            if best_bid and best_ask
            else "",
            "spread": str(best_ask - best_bid) if best_bid and best_ask else "",
            "bid_depth": str(sum(level.quantity for level in bid_levels)),
            "ask_depth": str(sum(level.quantity for level in ask_levels)),
            "last_trade_price": str(last_trade_price) if last_trade_price else "",
            "last_trade_quantity": str(last_trade_quantity)
            if last_trade_quantity
            else "",
        }

        stream_key = self._get_stream_key(stock_id)
        await self.redis.xadd(
            stream_key,
            cast(dict[FieldT, EncodableT], market_data),
            maxlen=self._window_size,
            approximate=True,
        )

    async def get_vwap(self, stock_id: UUID, window: timedelta) -> Decimal | None:
        """
        Gets the volume-weighted average price (VWAP) for a stock over a time
        window.

        Args:
            stock_id: Unique identifier of the stock
            window: Time window for the VWAP calculation

        Returns:
            VWAP for the stock over the time window.
        """
        # Calculate start time in milliseconds
        now = datetime.now(timezone.utc)
        start_time = int((now - window).timestamp() * 1000)

        # Format stream IDs properly for Redis
        start_id = f"{start_time}-0"
        end_id = f"{int(now.timestamp() * 1000)}-0"

        entries = await self.redis.xrange(
            self._get_stream_key(stock_id), min=start_id, max=end_id
        )

        if not entries:
            return None

        # Convert stream data to Polars DataFrame
        df = pl.DataFrame([{**entry[1], "stream_id": entry[0]} for entry in entries])

        return (
            df.filter(pl.col("last_trade_price") != "")
            .with_columns(
                [
                    pl.col("last_trade_price").cast(pl.Float64).alias("price"),
                    pl.col("last_trade_quantity").cast(pl.Float64).alias("quantity"),
                ]
            )
            .with_columns((pl.col("price") * pl.col("quantity")).alias("price_volume"))
            .select(
                [
                    (pl.col("price_volume").sum() / pl.col("quantity").sum()).alias(
                        "vwap"
                    )
                ]
            )
            .item()
        )

    async def get_market_depth(self, stock_id: UUID) -> dict:
        """
        Returns latest market depth statistics from Redis Stream.

        Args:
            stock_id: Unique identifier of the stock

        Returns:
            Dictionary containing the market depth statistics.
        """
        stream_key = self._get_stream_key(stock_id)

        # Get latest entry using proper Redis stream ID format
        latest = await self.redis.xrevrange(stream_key, max="+", min="-", count=1)
        if not latest:
            return {}

        entry = latest[0][1]
        return {
            "bid_depth": Decimal(entry["bid_depth"])
            if entry["bid_depth"]
            else Decimal("0"),
            "ask_depth": Decimal(entry["ask_depth"])
            if entry["ask_depth"]
            else Decimal("0"),
            "spread": Decimal(entry["spread"]) if entry["spread"] else None,
            "mid_price": Decimal(entry["mid_price"]) if entry["mid_price"] else None,
        }

    async def get_analytics_stream(
        self, stock_id: UUID, batch_size: int = 100
    ) -> AsyncGenerator[pl.DataFrame, None]:
        """
        Creates a real-time analytics stream using Redis Streams.

        Yields batches of market data as Polars DataFrames for real-time
        processing and analysis.

        Args:
            stock_id: Unique identifier of the stock
            batch_size: Number of entries to process in each batch

        Yields:
            Polars DataFrame containing a batch of market data entries
        """
        stream_key = self._get_stream_key(stock_id)
        last_id = "0"  # Start from beginning

        while True:
            # Get next batch of entries
            entries = await self.redis.xread(
                {stream_key: last_id}, count=batch_size, block=1000
            )

            if entries:
                stream_entries = entries[0][1]
                last_id = stream_entries[-1][0]

                # Process batch with Polars
                df = pl.DataFrame(
                    [{**entry[1], "stream_id": entry[0]} for entry in stream_entries]
                )

                yield df
