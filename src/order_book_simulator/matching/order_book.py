from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid5

from sortedcontainers import SortedDict

from order_book_simulator.common.models import (
    Delta,
    DeltaType,
    FilledOrder,
    OrderBookEntry,
    OrderSide,
    OrderType,
    PriceLevel,
)
from order_book_simulator.matching.delta_buffer import DeltaBuffer

# Fixed namespace for deterministic UUIDv5 fill IDs across replays.
FILL_ID_NAMESPACE = UUID("6f1e9c2a-8b4d-4f10-a91e-3c7d5e2b4a01")
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def _coerce_uuid(value: UUID | str) -> UUID:
    """Normalise a UUID or string representation to a UUID."""
    return value if isinstance(value, UUID) else UUID(str(value))


class OrderRejectionError(ValueError):
    """Raised when an order fails deterministic matching-domain validation."""


class OrderBook:
    """
    Manages the limit order book for a single stock.
    """

    SUPPORTED_ORDER_TYPES = frozenset({OrderType.LIMIT, OrderType.MARKET})

    def __init__(self, stock_id: UUID, ticker: str):
        self.stock_id: UUID = stock_id
        self.ticker: str = ticker
        self.bid_levels: SortedDict[Decimal, PriceLevel] = SortedDict(lambda x: -x)
        self.ask_levels: SortedDict[Decimal, PriceLevel] = SortedDict()
        # Map the order ID to the order object for O(1) look-ups, which enables
        # us to fetch order details and cancel / modify orders efficiently.
        self.order_id_to_order: dict[UUID, OrderBookEntry] = {}
        self.processed_order_ids: set[UUID] = set()
        self.delta_buffer: DeltaBuffer = DeltaBuffer()
        self._last_deltas: list[Delta] = []

    @property
    def last_deltas(self) -> list[Delta]:
        """Return deltas emitted by the most recent order or cancel call."""
        return list(self._last_deltas)

    def _record_delta(
        self,
        delta_type: DeltaType,
        side: OrderSide | None,
        price: Decimal,
        quantity: Decimal,
        order_count: int | None = None,
        trade_id: UUID | None = None,
        timestamp: float | None = None,
    ) -> Delta:
        """Append a delta to both the bounded history and current-operation list."""
        delta = self.delta_buffer.add(
            delta_type,
            self.ticker,
            side,
            price,
            quantity,
            order_count=order_count,
            trade_id=trade_id,
            timestamp=timestamp,
        )
        self._last_deltas.append(delta)
        return delta

    def _match_orders(
        self,
        incoming_order: OrderBookEntry,
        opposing_levels: SortedDict[Decimal, PriceLevel],
        is_buy: bool,
        event_timestamp: float,
    ) -> list[FilledOrder]:
        """
        Matches an incoming order against resting orders in the order book.

        Args:
            incoming_order: The order attempting to match.
            opposing_levels: The price levels to match against.
            is_buy: True if the incoming order is a buy order.
            event_timestamp: Deterministic event timestamp in Unix seconds.

        Returns:
            A list of fills from matching the order.
        """
        trades: list[FilledOrder] = []

        # Exit early if there are no opposing levels to match against.
        if not opposing_levels:
            return []

        # Track the price levels that need to be removed after matching.
        levels_to_remove: list[Decimal] = []

        for price, level in opposing_levels.items():
            if incoming_order.quantity <= Decimal(0):
                break

            # Check if the price level can match.
            if incoming_order.price is not None:
                if is_buy and price > incoming_order.price:
                    break
                if not is_buy and price < incoming_order.price:
                    break

            # Match against orders at this price level (FIFO via dict order).
            orders_to_remove: list[UUID] = []
            matched_at_level = False
            for order_id, resting_order in level.orders.items():
                if incoming_order.quantity <= Decimal(0):
                    break

                # Match the maximum quantity possible.
                match_quantity = min(incoming_order.quantity, resting_order.quantity)
                incoming_order.quantity -= match_quantity
                resting_order.quantity -= match_quantity
                fill_id = uuid5(
                    FILL_ID_NAMESPACE,
                    f"{self.stock_id}:{incoming_order.id}:{order_id}:{len(trades)}",
                )
                fill = FilledOrder(
                    id=fill_id,
                    stock_id=self.stock_id,
                    price=price,
                    quantity=match_quantity,
                    buyer_order_id=incoming_order.id if is_buy else order_id,
                    seller_order_id=order_id if is_buy else incoming_order.id,
                )
                trades.append(fill)
                matched_at_level = True
                self._record_delta(
                    DeltaType.TRADE,
                    None,
                    price,
                    match_quantity,
                    trade_id=fill.id,
                    timestamp=event_timestamp,
                )

                # If the resting order has been filled, remove it later.
                if resting_order.quantity == Decimal(0):
                    orders_to_remove.append(order_id)

            # Remove filled orders from level and order index.
            for order_id in orders_to_remove:
                del level.orders[order_id]
                del self.order_id_to_order[order_id]

            # Emit a level delta if this level was affected by matching.
            resting_side = OrderSide.SELL if is_buy else OrderSide.BUY
            if orders_to_remove:
                if not level.orders:
                    levels_to_remove.append(price)
                    self._record_delta(
                        DeltaType.LEVEL_REMOVE,
                        resting_side,
                        price,
                        Decimal(0),
                        0,
                        timestamp=event_timestamp,
                    )
                else:
                    self._record_delta(
                        DeltaType.LEVEL_UPDATE,
                        resting_side,
                        price,
                        level.quantity,
                        level.order_count,
                        timestamp=event_timestamp,
                    )
            elif matched_at_level:
                # Partial fill - no orders removed but quantity changed.
                self._record_delta(
                    DeltaType.LEVEL_UPDATE,
                    resting_side,
                    price,
                    level.quantity,
                    level.order_count,
                    timestamp=event_timestamp,
                )

        # Remove empty price levels.
        for price_level in levels_to_remove:
            del opposing_levels[price_level]

        return trades

    def _validate_order(
        self,
        order_type: OrderType,
        quantity: Decimal,
        price: Decimal | None,
    ) -> None:
        """
        Validates order semantics before the order can mutate the book.

        Args:
            order_type: The order type to validate.
            quantity: The requested quantity.
            price: The optional limit price.

        Raises:
            OrderRejectionError: If the order cannot be handled by the
                matching engine.
        """
        if order_type not in self.SUPPORTED_ORDER_TYPES:
            raise OrderRejectionError(f"{order_type.value} orders are not supported.")

        if not quantity.is_finite() or quantity <= Decimal(0):
            raise OrderRejectionError("Order quantity must be positive.")

        if order_type == OrderType.MARKET:
            if price is not None:
                raise OrderRejectionError("Market orders should not have a price.")
            return

        if price is None:
            raise OrderRejectionError("Limit orders must have a price.")

        if not price.is_finite() or price <= Decimal(0):
            raise OrderRejectionError("Limit order price must be positive.")

    def add_order(self, order: dict[str, Any]) -> list[dict]:
        """
        Processes an incoming order, matching it against existing orders and
        adding it to the book if it doesn't fully match.

        Args:
            order: The order to process.

        Returns:
            A list of trades that were executed.
        """
        # Coerce the order entries to the correct types.
        side = order["side"]
        if isinstance(side, str):
            side = OrderSide(side)
            order["side"] = side
        order_type = order["order_type"]
        if isinstance(order_type, str):
            order_type = OrderType(order_type)
            order["order_type"] = order_type
        is_buy = side == OrderSide.BUY
        price = Decimal(str(order["price"])) if order.get("price") is not None else None
        quantity = Decimal(str(order["quantity"]))
        self._validate_order(order_type, quantity, price)
        self._last_deltas = []

        order_id = _coerce_uuid(order["id"])
        if order_id in self.processed_order_ids:
            return []

        raw_created_at = order.get("created_at")
        if isinstance(raw_created_at, datetime):
            created_at = (
                raw_created_at
                if raw_created_at.tzinfo is not None
                else raw_created_at.replace(tzinfo=UTC)
            )
        elif isinstance(raw_created_at, str):
            parsed = datetime.fromisoformat(raw_created_at)
            created_at = (
                parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
            )
        else:
            created_at = datetime.now(UTC)
        event_timestamp = created_at.timestamp()
        entry_time_us = (created_at - EPOCH) // timedelta(microseconds=1)

        # Create an OrderBookEntry from the incoming order dict.
        incoming_order = OrderBookEntry(
            id=order_id,
            order_type=order_type,
            side=side,
            quantity=quantity,
            price=price,
            entry_time=entry_time_us,
        )
        self.processed_order_ids.add(order_id)

        # Match against the opposite side of the book.
        opposing_orders = self.ask_levels if is_buy else self.bid_levels
        fills: list[FilledOrder] = self._match_orders(
            incoming_order, opposing_orders, is_buy, event_timestamp
        )

        # If it's a limit order and there's remaining quantity, add it to the
        # book.
        if order_type == OrderType.LIMIT and incoming_order.quantity > 0:
            if price is None:
                raise OrderRejectionError("Limit orders must have a price.")

            price_levels = self.bid_levels if is_buy else self.ask_levels

            # Get or create the price level.
            if price not in price_levels:
                price_levels[price] = PriceLevel(price)
            price_level = price_levels[price]

            # Add the order to the price level and update the index and buffer.
            price_level.orders[incoming_order.id] = incoming_order
            self.order_id_to_order[incoming_order.id] = incoming_order
            self._record_delta(
                DeltaType.LEVEL_UPDATE,
                incoming_order.side,
                price,
                price_level.quantity,
                price_level.order_count,
                timestamp=event_timestamp,
            )

        trade_timestamp = created_at.isoformat()
        return [
            {
                "id": fill.id,
                "price": fill.price,
                "quantity": fill.quantity,
                "buyer_order_id": fill.buyer_order_id,
                "seller_order_id": fill.seller_order_id,
                "stock_id": self.stock_id,
                "timestamp": trade_timestamp,
            }
            for fill in fills
        ]

    def cancel_order(
        self, order_id: UUID | str, timestamp: float | None = None
    ) -> bool:
        """
        Cancels an order from the order book.

        Args:
            order_id: The ID of the order to cancel.
            timestamp: Optional deterministic cancel timestamp (Unix seconds).

        Returns:
            True if the order was cancelled, False otherwise.
        """
        self._last_deltas = []
        normalised_id = _coerce_uuid(order_id)
        order: OrderBookEntry | None = self.order_id_to_order.get(normalised_id)
        if not order:
            return False

        # Remove from the price level if it's a limit order.
        levels: SortedDict[Decimal, PriceLevel] = (
            self.bid_levels if order.side == OrderSide.BUY else self.ask_levels
        )
        if order.price is not None and order.price in levels:
            price_level: PriceLevel = levels[order.price]
            if normalised_id in price_level.orders:
                del price_level.orders[normalised_id]
                if not price_level.orders:
                    del levels[order.price]
                    self._record_delta(
                        DeltaType.LEVEL_REMOVE,
                        order.side,
                        order.price,
                        Decimal(0),
                        0,
                        timestamp=timestamp,
                    )
                else:
                    self._record_delta(
                        DeltaType.LEVEL_UPDATE,
                        order.side,
                        order.price,
                        price_level.quantity,
                        price_level.order_count,
                        timestamp=timestamp,
                    )

        # Remove from the order tracking.
        del self.order_id_to_order[normalised_id]
        return True

    def get_full_snapshot(self) -> dict:
        """
        Returns a snapshot of the current full order book state.

        Returns:
            A dictionary containing the current state of the order book.
        """
        return {
            "bids": [
                {
                    "price": str(price),
                    "quantity": str(level.quantity),
                    "order_count": level.order_count,
                }
                # Sort bids from high to low.
                for price, level in self.bid_levels.items()
            ],
            "asks": [
                {
                    "price": str(price),
                    "quantity": str(level.quantity),
                    "order_count": level.order_count,
                }
                # Sort asks from low to high.
                for price, level in self.ask_levels.items()
            ],
        }
