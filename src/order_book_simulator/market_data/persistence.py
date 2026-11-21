from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from order_book_simulator.common.events import DEFAULT_USER_ID
from order_book_simulator.common.models import OrderStatus, OrderType


def _parse_timestamp(raw_value: Any) -> datetime:
    """Resolve an ISO timestamp string or datetime into a UTC datetime."""
    if isinstance(raw_value, datetime):
        return (
            raw_value if raw_value.tzinfo is not None else raw_value.replace(tzinfo=UTC)
        )
    if isinstance(raw_value, str):
        parsed = datetime.fromisoformat(raw_value)
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
    return datetime.now(UTC)


def _coerce_uuid(value: Any) -> UUID:
    """Coerce a UUID or string value to a UUID instance."""
    return value if isinstance(value, UUID) else UUID(str(value))


async def persist_market_snapshot(
    stock_id: UUID, snapshot: dict[str, Any], db: AsyncSession
) -> None:
    """Persist a market snapshot to PostgreSQL.

    Args:
        stock_id: The stock ID.
        snapshot: The order book snapshot containing bids and asks.
        db: Database session.
    """
    query = text("""
        INSERT INTO market_snapshot (stock_id, timestamp, bids, asks)
        VALUES (:stock_id, :timestamp, :bids, :asks)
    """).bindparams(
        bindparam("bids", type_=JSONB),
        bindparam("asks", type_=JSONB),
    )

    await db.execute(
        query,
        {
            "stock_id": stock_id,
            "timestamp": _parse_timestamp(snapshot.get("timestamp")),
            "bids": snapshot["bids"],
            "asks": snapshot["asks"],
        },
    )


async def persist_order(order: dict[str, Any], db: AsyncSession) -> None:
    """Upsert a canonical order row in PostgreSQL with monotonic progression.

    Preserves immutable order metadata and never regresses filled_quantity or
    terminal order statuses on at-least-once Kafka redelivery.

    Args:
        order: Canonical order state update dictionary.
        db: Database session.
    """
    query = text("""
        INSERT INTO order_ (
            id, stock_id, side, price, quantity,
            filled_quantity, total_fee, status,
            time_in_force, client_order_id,
            created_at, updated_at,
            type, user_id
        )
        VALUES (
            :order_id, :stock_id, :side, :price, :quantity,
            :filled_quantity, :total_fee, :status,
            :time_in_force, :client_order_id,
            :created_at, :updated_at,
            :type, :user_id
        )
        ON CONFLICT (id) DO UPDATE SET
            user_id = EXCLUDED.user_id,
            type = EXCLUDED.type,
            side = EXCLUDED.side,
            price = EXCLUDED.price,
            quantity = EXCLUDED.quantity,
            time_in_force = EXCLUDED.time_in_force,
            client_order_id = EXCLUDED.client_order_id,
            created_at = EXCLUDED.created_at,
            filled_quantity = GREATEST(
                order_.filled_quantity, EXCLUDED.filled_quantity
            ),
            total_fee = GREATEST(order_.total_fee, EXCLUDED.total_fee),
            status = CASE
                WHEN order_.status = 'FILLED' THEN order_.status
                WHEN EXCLUDED.status = 'FILLED' THEN EXCLUDED.status
                WHEN order_.status = 'CANCELLED' THEN order_.status
                WHEN EXCLUDED.status = 'CANCELLED' THEN EXCLUDED.status
                WHEN order_.status = 'PARTIALLY_FILLED'
                    AND EXCLUDED.status = 'PENDING'
                    THEN order_.status
                ELSE EXCLUDED.status
            END,
            updated_at = GREATEST(order_.updated_at, EXCLUDED.updated_at)
    """)

    raw_price = order.get("price")
    created_at = _parse_timestamp(order.get("created_at"))
    updated_at = _parse_timestamp(order.get("updated_at") or created_at)
    raw_user_id = order.get("user_id")

    await db.execute(
        query,
        {
            "order_id": _coerce_uuid(order["id"]),
            "stock_id": _coerce_uuid(order["stock_id"]),
            "side": order["side"],
            "price": Decimal(str(raw_price)) if raw_price is not None else None,
            "quantity": Decimal(str(order["quantity"])),
            "filled_quantity": Decimal(str(order.get("filled_quantity", 0))),
            "total_fee": Decimal(str(order.get("total_fee", 0))),
            "status": order.get("status", OrderStatus.PENDING.value),
            "time_in_force": order.get("time_in_force"),
            "client_order_id": order.get("client_order_id"),
            "created_at": created_at,
            "updated_at": updated_at,
            "type": order.get("type", OrderType.LIMIT.value),
            "user_id": (
                _coerce_uuid(raw_user_id)
                if raw_user_id is not None
                else DEFAULT_USER_ID
            ),
        },
    )


async def _persist_fallback_order_stub(order: dict[str, Any], db: AsyncSession) -> None:
    """Insert a fallback order row only when a legacy event omitted order_updates."""
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
            "order_id": _coerce_uuid(order["id"]),
            "stock_id": _coerce_uuid(order["stock_id"]),
            "side": order["side"],
            "price": Decimal(str(order["price"])),
            "quantity": Decimal(str(order["quantity"])),
            "filled_quantity": Decimal(0),
            "status": OrderStatus.PENDING.value,
            "created_at": _parse_timestamp(order.get("created_at")),
            "type": OrderType.LIMIT.value,
            "user_id": DEFAULT_USER_ID,
        },
    )


async def persist_trades(
    stock_id: UUID,
    trades: list[dict[str, Any]],
    db: AsyncSession,
    *,
    known_order_ids: set[UUID] | None = None,
) -> None:
    """Persist trades to PostgreSQL idempotently.

    When canonical order_updates have already been persisted in the same
    transaction, pass their IDs in known_order_ids so no stub rows are needed.
    For legacy events without order_updates, missing order rows are created
    with ON CONFLICT DO NOTHING so real order rows are never overwritten.

    Args:
        stock_id: The stock ID.
        trades: List of trade records.
        db: Database session.
        known_order_ids: Order IDs already upserted in the current transaction.
    """
    covered = set(known_order_ids) if known_order_ids else set()
    for trade in trades:
        timestamp = _parse_timestamp(trade.get("timestamp"))
        buyer_id = _coerce_uuid(trade["buyer_order_id"])
        if buyer_id not in covered:
            await _persist_fallback_order_stub(
                {
                    "id": buyer_id,
                    "stock_id": stock_id,
                    "side": "BUY",
                    "price": trade["price"],
                    "quantity": trade["quantity"],
                    "created_at": timestamp,
                },
                db,
            )
            covered.add(buyer_id)

        seller_id = _coerce_uuid(trade["seller_order_id"])
        if seller_id not in covered:
            await _persist_fallback_order_stub(
                {
                    "id": seller_id,
                    "stock_id": stock_id,
                    "side": "SELL",
                    "price": trade["price"],
                    "quantity": trade["quantity"],
                    "created_at": timestamp,
                },
                db,
            )
            covered.add(seller_id)

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
        ON CONFLICT (id) DO NOTHING
    """)

    for trade in trades:
        # Preserve the matcher's fill ID so redelivered batches are idempotent.
        trade_id = _coerce_uuid(trade["id"]) if trade.get("id") is not None else uuid4()
        price = Decimal(str(trade["price"]))
        quantity = Decimal(str(trade["quantity"]))
        timestamp = _parse_timestamp(trade.get("timestamp"))

        await db.execute(
            query,
            {
                "id": trade_id,
                "stock_id": stock_id,
                "price": price,
                "quantity": quantity,
                "buyer_id": _coerce_uuid(trade["buyer_order_id"]),
                "seller_id": _coerce_uuid(trade["seller_order_id"]),
                "timestamp": timestamp,
                "total_amount": price * quantity,
                "buyer_fee": Decimal(str(trade.get("buyer_fee", 0))),
                "seller_fee": Decimal(str(trade.get("seller_fee", 0))),
            },
        )
