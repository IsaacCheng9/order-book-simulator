from decimal import Decimal
from typing import Any
from uuid import UUID

from sortedcontainers import SortedDict

from order_book_simulator.common.models import (
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
        self.order_to_price_level: dict[UUID, Decimal] = {}

    def _match_orders(
        self,
        incoming_order: dict[str, Any],
        opposing_levels: SortedDict[Decimal, PriceLevel],
        is_buy: bool,
    ) -> tuple[list[dict], Decimal]:
        """
        Matches an incoming order against resting orders in the order book.

        Args:
            incoming_order: The order attempting to match.
            opposing_levels: The price levels to match against.
            is_buy: True if the incoming order is a buy order.

        Returns:
            A tuple containing the list of trades and the remaining quantity.
        """
        trades: list[dict[str, Any]] = []
        remaining_quantity = Decimal(str(incoming_order["quantity"]))
        # May not have a price if it's a market order.
        limit_price: Decimal | None = (
            Decimal(str(incoming_order["price"]))
            if incoming_order.get("price") is not None
            else None
        )

        # Only limit orders should have a price.
        order_type = incoming_order["type"]
        if isinstance(order_type, str):
            order_type = OrderType(order_type)
        if order_type == OrderType.MARKET and limit_price is not None:
            raise ValueError("Market orders should not have a price.")
        if order_type == OrderType.LIMIT and limit_price is None:
            raise ValueError("Limit orders must have a price.")

        # Track the price levels that need to be removed after matching.
        levels_to_remove: list[Decimal] = []

        for price, level in opposing_levels.items():
            if remaining_quantity <= Decimal(0):
                break

            # Check if the price level can match.
            if limit_price:
                if is_buy and price > limit_price:
                    break
                if not is_buy and price < limit_price:
                    break

            # Match against orders at this price level (FIFO via dict order).
            orders_to_remove: list[UUID] = []
            for order_id, resting_order in level.orders.items():
                if remaining_quantity <= Decimal(0):
                    break

                # Match the maximum quantity possible.
                match_quantity = min(remaining_quantity, resting_order.quantity)
                remaining_quantity -= match_quantity
                resting_order.quantity -= match_quantity
                trade = {
                    "price": price,
                    "quantity": match_quantity,
                    "buyer_order_id": incoming_order["id"] if is_buy else order_id,
                    "seller_order_id": order_id if is_buy else incoming_order["id"],
                    "stock_id": self.stock_id,
                }
                trades.append(trade)
                # If the resting order has been filled, remove it later.
                if resting_order.quantity == Decimal(0):
                    orders_to_remove.append(order_id)

            # Remove filled orders from level and order index.
            for order_id in orders_to_remove:
                del level.orders[order_id]
                del self.order_to_price_level[order_id]
            # Mark empty price levels for removal.
            if not level.orders:
                levels_to_remove.append(price)

        # Remove empty price levels.
        for price_level in levels_to_remove:
            del opposing_levels[price_level]

        return trades, remaining_quantity

    def _insert_order_with_price_time_priority(
        self,
        order: OrderBookEntry,
        orders: list[OrderBookEntry],
        is_buy: bool,
    ) -> None:
        """
        Inserts an order into the appropriate position, maintaining price-time
        priority.

        Args:
            order: The order to insert.
            orders: The list of orders to insert into.
            is_buy: True if this is for the buy side.
        """
        insert_index = 0

        # Find the correct position to insert the order.
        for existing_order in orders:
            if (
                # Buy orders should prioritise higher prices, then earlier
                # entry times.
                is_buy
                and (
                    existing_order.price < order.price
                    or (
                        existing_order.price == order.price
                        and existing_order.entry_time > order.entry_time
                    )
                )
            ) or (
                # Sell orders should prioritise lower prices, then earlier
                # entry times.
                not is_buy
                and (
                    existing_order.price > order.price
                    or (
                        existing_order.price == order.price
                        and existing_order.entry_time > order.entry_time
                    )
                )
            ):
                break
            insert_index += 1

        orders.insert(insert_index, order)

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
        trades: list[dict[str, Any]] = []

        # Match against the opposite side of the book if prices overlap.
        opposing_orders = self.ask_levels if is_buy else self.bid_levels
        trades, remaining_quantity = self._match_orders(order, opposing_orders, is_buy)

        # If it's a limit order and there's remaining quantity, add it to the
        # book.
        if order_type == OrderType.LIMIT and remaining_quantity > 0:
            price = Decimal(str(order["price"]))
            entry = OrderBookEntry(
                id=order["id"],
                price=price,
                quantity=remaining_quantity,
                # Convert the timestamp to microseconds.
                entry_time=int(order["created_at"].timestamp() * 1_000_000),
            )

            # Update or create the price level.
            aggregated_levels = self.bids if is_buy else self.asks
            if price in aggregated_levels:
                aggregated_level = aggregated_levels[price]
                aggregated_level.quantity += remaining_quantity
                aggregated_level.order_count += 1
            else:
                aggregated_levels[price] = PriceLevel(
                    price=price,
                    quantity=remaining_quantity,
                    order_count=1,
                )

            # Add the order to the approrpiate list with price-time priority.
            orders = self._bid_orders if is_buy else self._ask_orders
            self._insert_order_with_price_time_priority(entry, orders, is_buy)

        return trades

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
                for price, level in sorted(
                    self.bid_levels.items(),
                    key=lambda x: x[0],
                    reverse=True,
                )
            ],
            "asks": [
                {
                    "price": str(price),
                    "quantity": str(level.quantity),
                    "order_count": level.order_count,
                }
                # Sort asks from low to high.
                for price, level in sorted(
                    self.ask_levels.items(),
                    key=lambda x: x[0],
                )
            ],
        }
