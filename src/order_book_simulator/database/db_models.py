from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    DECIMAL,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Uuid,
)
from sqlalchemy import (
    Enum as SqlEnum,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from order_book_simulator.common.models import OrderSide, OrderStatus, OrderType


class Base(DeclarativeBase):
    pass


class Stock(Base):
    __tablename__ = "stock"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    ticker: Mapped[str] = mapped_column(String(20))
    company_name: Mapped[str] = mapped_column(String(255))
    min_order_size: Mapped[Decimal] = mapped_column(DECIMAL(20, 8))
    max_order_size: Mapped[Decimal] = mapped_column(DECIMAL(20, 8))
    price_precision: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    # Relationships
    orders: Mapped[list["Order"]] = relationship("Order", back_populates="stock")
    trades: Mapped[list["Trade"]] = relationship("Trade", back_populates="stock")


class Order(Base):
    __tablename__ = "order_"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    user_id: Mapped[UUID] = mapped_column(Uuid)
    stock_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("stock.id"))
    type: Mapped[OrderType] = mapped_column(SqlEnum(OrderType))
    side: Mapped[OrderSide] = mapped_column(SqlEnum(OrderSide))
    status: Mapped[OrderStatus] = mapped_column(SqlEnum(OrderStatus))
    price: Mapped[Decimal | None] = mapped_column(DECIMAL(20, 8), nullable=True)
    quantity: Mapped[Decimal] = mapped_column(DECIMAL(20, 8))
    filled_quantity: Mapped[Decimal] = mapped_column(DECIMAL(20, 8))
    total_fee: Mapped[Decimal] = mapped_column(DECIMAL(20, 8))
    time_in_force: Mapped[str | None] = mapped_column(String(20), nullable=True)
    client_order_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    # Relationships
    stock: Mapped["Stock"] = relationship("Stock", back_populates="orders")
    buy_trades: Mapped[list["Trade"]] = relationship(
        "Trade", foreign_keys="Trade.buyer_order_id", back_populates="buyer_order"
    )
    sell_trades: Mapped[list["Trade"]] = relationship(
        "Trade", foreign_keys="Trade.seller_order_id", back_populates="seller_order"
    )


class Trade(Base):
    __tablename__ = "trade"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    buyer_order_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("order_.id"))
    seller_order_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("order_.id"))
    stock_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("stock.id"))
    price: Mapped[Decimal] = mapped_column(DECIMAL(20, 8))
    quantity: Mapped[Decimal] = mapped_column(DECIMAL(20, 8))
    total_amount: Mapped[Decimal] = mapped_column(DECIMAL(20, 8))
    buyer_fee: Mapped[Decimal] = mapped_column(DECIMAL(20, 8))
    seller_fee: Mapped[Decimal] = mapped_column(DECIMAL(20, 8))
    trade_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    # Relationships
    buyer_order: Mapped[Order] = relationship(
        "Order", foreign_keys=[buyer_order_id], back_populates="buy_trades"
    )
    seller_order: Mapped[Order] = relationship(
        "Order", foreign_keys=[seller_order_id], back_populates="sell_trades"
    )
    stock: Mapped[Stock] = relationship("Stock", back_populates="trades")
