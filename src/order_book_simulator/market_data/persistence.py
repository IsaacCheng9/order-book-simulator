import orjson
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

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
            "bids": orjson.dumps(snapshot["bids"]),
            "asks": orjson.dumps(snapshot["asks"]),
        },
    )


async def persist_order(order: dict[str, Any], db: AsyncSession) -> None:
    """
    Persists an order to PostgreSQL.

    Args:
        order: The order data
        db: Database session
    """
    query = text("""
        INSERT INTO order_ (
            id, stock_id, side, price, quantity, 
            filled_quantity, status, created_at,
            type, user_id
        )
        VALUES (
            :order_id, :stock_id, :side, :price, :quantity,
            :filled_quantity, :status, :created_at,
            :type, :user_id
        )
        ON CONFLICT (id) DO NOTHING
    """)

    await db.execute(
        query,
        {
            "order_id": order["id"],
            "stock_id": order["stock_id"],
            "side": order["side"],
            "price": Decimal(str(order["price"])),
            "quantity": Decimal(str(order["quantity"])),
            "filled_quantity": Decimal("0"),
            "status": "PENDING",
            "created_at": order.get("created_at", datetime.now(timezone.utc)),
            "type": "LIMIT",  # Default to LIMIT orders for now
            "user_id": UUID("00000000-0000-0000-0000-000000000000"),  # Default user ID
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
    for trade in trades:
        buyer_order = {
            "id": trade["buyer_order_id"],
            "stock_id": stock_id,
            "side": "BUY",
            "price": trade["price"],
            "quantity": trade["quantity"],
            "created_at": datetime.fromisoformat(trade["timestamp"])
            if isinstance(trade["timestamp"], str)
            else trade["timestamp"],
        }
        await persist_order(buyer_order, db)

        seller_order = {
            "id": trade["seller_order_id"],
            "stock_id": stock_id,
            "side": "SELL",
            "price": trade["price"],
            "quantity": trade["quantity"],
            "created_at": datetime.fromisoformat(trade["timestamp"])
            if isinstance(trade["timestamp"], str)
            else trade["timestamp"],
        }
        await persist_order(seller_order, db)

    # Now persist the trades
    query = text("""
        INSERT INTO trade (
            id, stock_id, price, quantity, buyer_order_id, 
            seller_order_id, trade_time, total_amount,
            buyer_fee, seller_fee
        )
        VALUES (
            :id, :stock_id, :price, :quantity, :buyer_id,
            :seller_id, :timestamp, :total_amount,
            :buyer_fee, :seller_fee
        )
    """)

    for trade in trades:
        # Calculate total amount from price and quantity.
        price = Decimal(str(trade["price"]))
        quantity = Decimal(str(trade["quantity"]))
        # Convert timestamp string to datetime if needed.
        timestamp = (
            datetime.fromisoformat(trade["timestamp"])
            if isinstance(trade["timestamp"], str)
            else trade.get("timestamp", datetime.now(timezone.utc))
        )

        await db.execute(
            query,
            {
                "id": uuid4(),
                "stock_id": stock_id,
                "price": price,
                "quantity": quantity,
                "buyer_id": trade["buyer_order_id"],
                "seller_id": trade["seller_order_id"],
                "timestamp": timestamp,
                "total_amount": price * quantity,
                "buyer_fee": trade.get("buyer_fee", 0),
                "seller_fee": trade.get("seller_fee", 0),
            },
        )
