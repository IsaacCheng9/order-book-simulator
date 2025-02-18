import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def persist_market_snapshot(
    stock_id: UUID, snapshot: dict[str, Any], db: AsyncSession
) -> None:
    """
    Persists a market snapshot to PostgreSQL.

    Args:
        stock_id: The stock ID
        snapshot: The order book snapshot containing bids and asks
        db: Database session
    """
    query = text("""
        INSERT INTO market_snapshot (stock_id, timestamp, bids, asks)
        VALUES (:stock_id, :timestamp, :bids, :asks)
    """)

    await db.execute(
        query,
        {
            "stock_id": stock_id,
            "timestamp": datetime.now(),
            "bids": json.dumps(snapshot["bids"]),
            "asks": json.dumps(snapshot["asks"]),
        },
    )


async def persist_trades(stock_id: UUID, trades: list[dict], db: AsyncSession) -> None:
    """
    Persists trades to PostgreSQL.

    Args:
        stock_id: The stock ID
        trades: List of trade records
        db: Database session
    """
    query = text("""
        INSERT INTO trade (
            stock_id, price, quantity, buyer_order_id, 
            seller_order_id, trade_time, total_amount,
            buyer_fee, seller_fee
        )
        VALUES (
            :stock_id, :price, :quantity, :buyer_id,
            :seller_id, :timestamp, :total_amount,
            :buyer_fee, :seller_fee
        )
    """)

    for trade in trades:
        # Calculate total amount from price and quantity
        price = Decimal(str(trade["price"]))
        quantity = Decimal(str(trade["quantity"]))
        total_amount = price * quantity
        
        # Convert timestamp string to datetime if it's a string
        timestamp = (
            datetime.fromisoformat(trade["timestamp"])
            if isinstance(trade["timestamp"], str)
            else trade.get("timestamp", datetime.now(timezone.utc))
        )

        await db.execute(
            query,
            {
                "stock_id": stock_id,
                "price": price,
                "quantity": quantity,
                "buyer_id": trade["buyer_order_id"],
                "seller_id": trade["seller_order_id"],
                "timestamp": timestamp,
                "total_amount": total_amount,
                "buyer_fee": trade.get("buyer_fee", 0),
                "seller_fee": trade.get("seller_fee", 0),
            },
        )
