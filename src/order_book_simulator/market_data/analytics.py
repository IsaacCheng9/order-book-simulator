from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, cast
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
        trades: list[dict[str, Any]] | None = None,
        sequence_number: int | None = None,
    ) -> None:
        """Record order book state to Redis Stream using actual PriceLevels.

        When sequence_number is supplied, skip appending if the latest stream
        entry already recorded that sequence number or a newer one so crash
        replay of an uncommitted Kafka offset does not duplicate entries.

        Args:
            stock_id: Unique identifier of the stock.
            bid_levels: List of bid price levels from the order book.
            ask_levels: List of ask price levels from the order book.
            last_trade_price: Price of the last trade, if any.
            last_trade_quantity: Quantity of the last trade, if any.
            trades: All fills executed during this book update, if any.
            sequence_number: Optional monotonic delta sequence watermark.
        """
        stream_key = self._get_stream_key(stock_id)
        if sequence_number is not None:
            latest = await self.redis.xrevrange(stream_key, max="+", min="-", count=1)
            if latest:
                latest_fields = latest[0][1]
                raw_seq = latest_fields.get("sequence_number")
                if raw_seq is None:
                    raw_seq = latest_fields.get(b"sequence_number")
                if raw_seq is not None:
                    decoded_seq = (
                        raw_seq.decode()
                        if isinstance(raw_seq, (bytes, bytearray))
                        else str(raw_seq)
                    )
                    if int(decoded_seq) >= sequence_number:
                        return

        best_bid = max(bid_levels, key=lambda x: x.price).price if bid_levels else None
        best_ask = min(ask_levels, key=lambda x: x.price).price if ask_levels else None

        trade_notional = Decimal(0)
        trade_volume = Decimal(0)
        if trades is not None:
            for trade in trades:
                trade_price = Decimal(str(trade["price"]))
                trade_qty = Decimal(str(trade["quantity"]))
                trade_notional += trade_price * trade_qty
                trade_volume += trade_qty
            if trades:
                if last_trade_price is None:
                    last_trade_price = Decimal(str(trades[-1]["price"]))
                if last_trade_quantity is None:
                    last_trade_quantity = Decimal(str(trades[-1]["quantity"]))
        elif last_trade_price is not None and last_trade_quantity is not None:
            trade_notional = last_trade_price * last_trade_quantity
            trade_volume = last_trade_quantity

        now = datetime.now(UTC)
        market_data: dict[str, Any] = {
            "timestamp": now.isoformat(),
            "mid_price": str((best_bid + best_ask) / 2)
            if best_bid is not None and best_ask is not None
            else "",
            "spread": str(best_ask - best_bid)
            if best_bid is not None and best_ask is not None
            else "",
            "bid_depth": str(sum(level.quantity for level in bid_levels)),
            "ask_depth": str(sum(level.quantity for level in ask_levels)),
            "last_trade_price": str(last_trade_price)
            if last_trade_price is not None
            else "",
            "last_trade_quantity": str(last_trade_quantity)
            if last_trade_quantity is not None
            else "",
            "trade_notional": str(trade_notional) if trade_volume > 0 else "",
            "trade_volume": str(trade_volume) if trade_volume > 0 else "",
        }
        if sequence_number is not None:
            market_data["sequence_number"] = str(sequence_number)

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
            VWAP for the stock over the time window, or None if no trades
            occurred in the window.
        """
        # Calculate start time in milliseconds.
        now = datetime.now(UTC)
        start_time = int((now - window).timestamp() * 1000)

        # Use '+' as the upper bound so entries in the current millisecond
        # with sequence > 0 (or slight clock skew) are included.
        start_id = f"{start_time}-0"
        entries = await self.redis.xrange(
            self._get_stream_key(stock_id), min=start_id, max="+"
        )

        if not entries:
            return None

        total_notional = Decimal(0)
        total_volume = Decimal(0)
        for _, fields in entries:
            has_notional, notional_raw = self._read_stream_field(
                fields, "trade_notional"
            )
            has_volume, volume_raw = self._read_stream_field(fields, "trade_volume")
            if has_notional and has_volume:
                if notional_raw != "" and volume_raw != "":
                    total_notional += Decimal(notional_raw)
                    total_volume += Decimal(volume_raw)
                continue

            _, price_raw = self._read_stream_field(fields, "last_trade_price")
            _, qty_raw = self._read_stream_field(fields, "last_trade_quantity")
            if price_raw != "" and qty_raw != "":
                price = Decimal(price_raw)
                quantity = Decimal(qty_raw)
                total_notional += price * quantity
                total_volume += quantity

        if total_volume <= Decimal(0):
            return None

        return total_notional / total_volume

    @staticmethod
    def _read_stream_field(fields: dict[Any, Any], name: str) -> tuple[bool, str]:
        """Read a string or bytes Redis stream field safely."""
        if name in fields:
            raw = fields[name]
        else:
            encoded = name.encode("utf-8")
            if encoded not in fields:
                return False, ""
            raw = fields[encoded]
        if isinstance(raw, (bytes, bytearray)):
            return True, raw.decode("utf-8")
        return True, str(raw)

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
            else Decimal(0),
            "ask_depth": Decimal(entry["ask_depth"])
            if entry["ask_depth"]
            else Decimal(0),
            "spread": Decimal(entry["spread"]) if entry["spread"] else None,
            "mid_price": Decimal(entry["mid_price"]) if entry["mid_price"] else None,
        }

    async def get_analytics_stream(
        self, stock_id: UUID, batch_size: int = 100
    ) -> AsyncGenerator[pl.DataFrame]:
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
