from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID


@dataclass
class PriceLevel:
    """Represents a price level in the order book with its aggregated quantity."""

    price: Decimal
    quantity: Decimal
    order_count: int


@dataclass
class OrderBookState:
    """Represents the current state of the order book."""

    stock_id: UUID
    ticker: str
    bids: list[PriceLevel]  # Sorted by price descending
    asks: list[PriceLevel]  # Sorted by price ascending
    last_trade_price: Decimal | None
    last_trade_quantity: Decimal | None
    last_update_time: datetime
