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
    ticker: str  # e.g., "AAPL", "MSFT"
    min_order_size: Decimal
    max_order_size: Decimal
    price_precision: int
