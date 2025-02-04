from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID
from order_book_simulator.common.models import OrderType, OrderSide, OrderStatus


@dataclass
class AggregatedLevel:
    """
    Represents an aggregated price level in the order book with total volume.
    """

    price: Decimal
    quantity: Decimal
    order_count: int


@dataclass
class OrderBookEntry:
    """
    Represents an individual order in the order book.
    """

    id: UUID
    price: Decimal
    quantity: Decimal
    entry_time: int


@dataclass
class OrderBook:
    """
    Manages the limit order book for a single instrument.
    """

    def __init__(self, instrument_id: UUID):
        self.instrument_id = instrument_id
        self.bids: dict[Decimal, AggregatedLevel] = {}
        self.asks: dict[Decimal, AggregatedLevel] = {}
        self._bid_orders: list[OrderBookEntry] = []
        self._ask_orders: list[OrderBookEntry] = []

    def add_order(self, order: dict[str, Any]) -> list[dict]:
        """
        Processes an incoming order, matching it against existing orders and
        adding it to the book if it doesn't fully match.

        Args:
            order: The order to process.

        Returns:
            A list of trades that were executed.
        """
        pass

    def _match_orders(
        self,
        incoming_order: dict[str, Any],
        resting_orders: list[OrderBookEntry],
        aggregated_levels: dict[Decimal, AggregatedLevel],
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
        trades = []
        remaining_quantity = Decimal(str(incoming_order["quantity"]))
        price = Decimal(str(incoming_order["price"]))

        # Iterate on a copy of the list so we can edit the original list.
        for resting_order in resting_orders[:]:
            if remaining_quantity <= 0:
                break

            # For market orders, match against any price.
            # For limit orders, match if their buy order is greater than or
            # equal to the lowest sell, or their sell order is less than or
            # equal to the highest buy.
            if (
                incoming_order["type"] == OrderType.MARKET
                or (is_buy and resting_order.price <= price)
                or (not is_buy and resting_order.price >= price)
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
                    "instrument_id": self.instrument_id,
                }
                trades.append(trade)

                # Update the resting order and level with the remaining
                # quantity.
                aggregated_level = aggregated_levels[resting_order.price]
                aggregated_level.quantity -= match_quantity
                resting_order.quantity -= match_quantity
                if resting_order.quantity == 0:
                    resting_orders.remove(resting_order)
                    aggregated_level.order_count -= 1
                    if aggregated_level.quantity == 0:
                        del aggregated_levels[resting_order.price]

        return trades, remaining_quantity
