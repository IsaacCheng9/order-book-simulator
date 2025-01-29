from datetime import datetime
from decimal import Decimal
from enum import Enum
from uuid import UUID
from pydantic import BaseModel


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderStatus(str, Enum):
    PENDING = "pending"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"


class OrderRequest(BaseModel):
    user_id: UUID
    instrument_id: UUID
    type: OrderType
    side: OrderSide
    price: Decimal | None = None
    quantity: Decimal
    time_in_force: str | None = None
    client_order_id: str | None = None


class OrderResponse(BaseModel):
    id: UUID
    user_id: UUID
    instrument_id: UUID
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
