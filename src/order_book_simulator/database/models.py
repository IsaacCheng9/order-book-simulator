from decimal import Decimal
from uuid import UUID

from sqlalchemy import DECIMAL, Integer, String, Uuid
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


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
