from decimal import Decimal
from typing import Any
from uuid import UUID

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
        self.bids: dict[Decimal, PriceLevel] = {}
        self.asks: dict[Decimal, PriceLevel] = {}
        self._bid_orders: list[OrderBookEntry] = []
        self._ask_orders: list[OrderBookEntry] = []

    def _match_orders(
        self,
        incoming_order: dict[str, Any],
        resting_orders: list[OrderBookEntry],
        aggregated_levels: dict[Decimal, PriceLevel],
        is_buy: bool,
    ) -> tuple[list[dict], Decimal]:
        """
        Matches an incoming order against resting orders in the order book.

        Args:
            incoming_order: The order attempting to match.
            resting_orders: The list of orders to match against.
            levels: The price level aggregations to update.
            is_buy: True if the incoming order is a buy order.

        Returns:
            A tuple containing the list of trades and the remaining quantity.
        """
        trades: list[dict[str, Any]] = []
        remaining_quantity = Decimal(str(incoming_order["quantity"]))
        # May not have a price if it's a market order.
        price: Decimal | None = (
            Decimal(str(incoming_order["price"]))
            if incoming_order.get("price") is not None
            else None
        )
        # Market orders have no price as they match against the best available.
        order_type = incoming_order["type"]
        if order_type == OrderType.MARKET and price is not None:
            raise ValueError("Market orders should not have a price.")
        if order_type != OrderType.MARKET and price is None:
            raise ValueError("Limit orders must have a price.")

        # Iterate on a copy of the list so we can edit the original list.
        for resting_order in resting_orders[:]:
            if remaining_quantity <= 0:
                break

            # For market orders, match against any price.
            # For limit orders, match if their buy order is greater than or
            # equal to the lowest sell, or their sell order is less than or
            # equal to the highest buy.
            if (
                order_type == OrderType.MARKET
                or (is_buy and price and resting_order.price <= price)
                or (not is_buy and price and resting_order.price >= price)
            ):
                match_quantity = min(remaining_quantity, resting_order.quantity)
                remaining_quantity -= match_quantity

                # Record the trade.
                trade = {
                    "price": resting_order.price,
                    "quantity": match_quantity,
                    "buyer_order_id": incoming_order["id"]
                    if is_buy
                    else resting_order.id,
                    "seller_order_id": incoming_order["id"]
                    if not is_buy
                    else resting_order.id,
                    "stock_id": self.stock_id,
                }
                trades.append(trade)

                # Update the resting order and level with the remaining
                # quantity.
                aggregated_level = aggregated_levels[resting_order.price]
                aggregated_level.quantity -= match_quantity
                resting_order.quantity -= match_quantity
                if resting_order.quantity == 0:
                    resting_orders.remove(resting_order)
                    # Reduce the order count by 1, and remove it if there are
                    # no orders left at this price.
                    aggregated_level.order_count -= 1
                    if aggregated_level.quantity == 0:
                        del aggregated_levels[resting_order.price]

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
        is_buy: bool = order["side"] == OrderSide.BUY
        trades: list[dict[str, Any]] = []

        # Match against the opposite side of the book if prices overlap.
        opposing_orders = self._ask_orders if is_buy else self._bid_orders
        opposing_aggregated_levels = self.asks if is_buy else self.bids
        trades, remaining_quantity = self._match_orders(
            order, opposing_orders, opposing_aggregated_levels, is_buy
        )

        # If it's a limit order and there's remaining quantity, add it to the
        # book.
        if order["type"] == OrderType.LIMIT and remaining_quantity > 0:
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
                    self.bids.items(),
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
                    self.asks.items(),
                    key=lambda x: x[0],
                )
            ],
        }
