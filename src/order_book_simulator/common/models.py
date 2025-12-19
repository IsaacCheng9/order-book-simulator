from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from uuid import UUID

from pydantic import BaseModel


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderStatus(str, Enum):
    PENDING = "PENDING"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"


class OrderRequest(BaseModel):
    user_id: UUID
    ticker: str
    type: OrderType
    side: OrderSide
    price: Decimal | None = None
    quantity: Decimal
    time_in_force: str | None = None
    client_order_id: str | None = None


class OrderResponse(BaseModel):
    id: UUID
    user_id: UUID
    ticker: str
    type: OrderType
    side: OrderSide
    status: OrderStatus
    price: Decimal | None
    quantity: Decimal
    filled_quantity: Decimal
    total_fee: Decimal
    time_in_force: str | None
    client_order_id: str | None
    created_at: datetime
    updated_at: datetime


class Stock(BaseModel):
    id: UUID
    ticker: str
    company_name: str
    min_order_size: Decimal
    max_order_size: Decimal
    price_precision: int


@dataclass
class OrderBookEntry:
    """
    Represents an individual order in the order book.
    """

    id: UUID
    price: Decimal
    quantity: Decimal
    # The time the order was added to the book in microseconds.
    entry_time: int


@dataclass
class FilledOrder:
    id: UUID
    stock_id: UUID
    price: Decimal
    quantity: Decimal
    buyer_order_id: UUID
    seller_order_id: UUID


@dataclass
class PriceLevel:
    """Represents a price level in the order book with its aggregated quantity."""

    price: Decimal
    orders: dict[UUID, OrderBookEntry] = field(default_factory=dict)

    @property
    def quantity(self) -> Decimal:
        return Decimal(sum(order.quantity for order in self.orders.values()))

    @property
    def order_count(self) -> int:
        return len(self.orders)


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
