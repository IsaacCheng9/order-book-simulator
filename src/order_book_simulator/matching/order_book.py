from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sortedcontainers import SortedDict

from order_book_simulator.common.models import (
    FilledOrder,
    OrderBookEntry,
    OrderSide,
    OrderType,
    PriceLevel,
)


class OrderBook:
    """
    Manages the limit order book for a single stock.
    """

    def __init__(self, stock_id: UUID):
        self.stock_id = stock_id
        self.bid_levels: SortedDict[Decimal, PriceLevel] = SortedDict(
            lambda x: -x  # type: ignore
        )
        self.ask_levels: SortedDict[Decimal, PriceLevel] = SortedDict()
        # Map the order ID to the order object for O(1) look-ups, which enables
        # us to fetch order details and cancel / modify orders efficiently.
        self.order_id_to_order: dict[UUID, OrderBookEntry] = {}

    def _match_orders(
        self,
        incoming_order: OrderBookEntry,
        opposing_levels: SortedDict[Decimal, PriceLevel],
        is_buy: bool,
    ) -> list[FilledOrder]:
        """
        Matches an incoming order against resting orders in the order book.

        Args:
            incoming_order: The order attempting to match.
            opposing_levels: The price levels to match against.
            is_buy: True if the incoming order is a buy order.

        Returns:
            A list of fills from matching the order.
        """
        # Exit early if there are no opposing levels to match against.
        if not opposing_levels:
            return []

        trades: list[FilledOrder] = []

        # Only limit orders should have a price.
        if (
            incoming_order.order_type == OrderType.MARKET
            and incoming_order.price is not None
        ):
            raise ValueError("Market orders should not have a price.")
        if (
            incoming_order.order_type == OrderType.LIMIT
            and incoming_order.price is None
        ):
            raise ValueError("Limit orders must have a price.")

        # Track the price levels that need to be removed after matching.
        levels_to_remove: list[Decimal] = []

        for price, level in opposing_levels.items():
            if incoming_order.quantity <= Decimal(0):
                break

            # Check if the price level can match.
            if incoming_order.price:
                if is_buy and price > incoming_order.price:
                    break
                if not is_buy and price < incoming_order.price:
                    break

            # Match against orders at this price level (FIFO via dict order).
            orders_to_remove: list[UUID] = []
            for order_id, resting_order in level.orders.items():
                if incoming_order.quantity <= Decimal(0):
                    break

                # Match the maximum quantity possible.
                match_quantity = min(incoming_order.quantity, resting_order.quantity)
                incoming_order.quantity -= match_quantity
                resting_order.quantity -= match_quantity
                fill = FilledOrder(
                    id=uuid4(),
                    stock_id=self.stock_id,
                    price=price,
                    quantity=match_quantity,
                    buyer_order_id=incoming_order.id if is_buy else order_id,
                    seller_order_id=order_id if is_buy else incoming_order.id,
                )
                trades.append(fill)
                # If the resting order has been filled, remove it later.
                if resting_order.quantity == Decimal(0):
                    orders_to_remove.append(order_id)

            # Remove filled orders from level and order index.
            for order_id in orders_to_remove:
                del level.orders[order_id]
                del self.order_id_to_order[order_id]
            # Mark empty price levels for removal.
            if not level.orders:
                levels_to_remove.append(price)

        # Remove empty price levels.
        for price_level in levels_to_remove:
            del opposing_levels[price_level]

        return trades

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
        order_type = order["type"]
        if isinstance(order_type, str):
            order_type = OrderType(order_type)
            order["type"] = order_type
        is_buy = side == OrderSide.BUY
        price = Decimal(order["price"]) if order.get("price") is not None else None

        # Create an OrderBookEntry from the incoming order dict.
        incoming_order = OrderBookEntry(
            id=order["id"],
            order_type=order_type,
            side=side,
            quantity=Decimal(order["quantity"]),
            price=price,
            entry_time=int(order["created_at"].timestamp() * 1_000_000),
        )

        # Match against the opposite side of the book.
        opposing_orders = self.ask_levels if is_buy else self.bid_levels
        fills: list[FilledOrder] = self._match_orders(
            incoming_order, opposing_orders, is_buy
        )

        # If it's a limit order and there's remaining quantity, add it to the
        # book.
        if order_type == OrderType.LIMIT and incoming_order.quantity > 0:
            if not price:
                raise ValueError("Limit orders must have a price.")

            price_levels = self.bid_levels if is_buy else self.ask_levels

            # Get or create the price level.
            if price not in price_levels:
                price_levels[price] = PriceLevel(price)
            price_level = price_levels[price]

            # Add the order to the price level and update the index.
            price_level.orders[incoming_order.id] = incoming_order
            self.order_id_to_order[incoming_order.id] = incoming_order

        return [
            {
                "price": fill.price,
                "quantity": fill.quantity,
                "buyer_order_id": fill.buyer_order_id,
                "seller_order_id": fill.seller_order_id,
                "stock_id": self.stock_id,
            }
            for fill in fills
        ]

    def cancel_order(self, order_id: UUID) -> bool:
        """
        Cancels an order from the order book.

        Args:
            order_id: The ID of the order to cancel.

        Returns:
            True if the order was cancelled, False otherwise.
        """
        order = self.order_id_to_order.get(order_id)
        if not order:
            return False

        # Remove from the price level if it's a limit order.
        levels = self.bid_levels if order.side == OrderSide.BUY else self.ask_levels
        if order.price and order.price in levels:
            price_level = levels[order.price]
            if order_id in price_level.orders:
                del price_level.orders[order_id]
                if not price_level.orders:
                    del levels[order.price]

        # Remove from the order tracking.
        del self.order_id_to_order[order_id]
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
