from sqlalchemy import String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Stock(Base):
    __tablename__ = "stock"

    id: Mapped[str] = mapped_column(primary_key=True)
    ticker: Mapped[str] = mapped_column(String(20))
