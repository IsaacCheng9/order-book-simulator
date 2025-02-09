from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException

from order_book_simulator.common.cache import order_book_cache

router = APIRouter(tags=["market-data"])


@router.get("/order-book/{instrument_id}")
async def get_order_book(instrument_id: UUID) -> dict[str, Any]:
    """Returns the current state of the order book for the specified instrument."""
    snapshot = order_book_cache.get_order_book(instrument_id)
    if not snapshot:
        raise HTTPException(
            status_code=404,
            detail=f"No order book found for instrument {instrument_id}",
        )

    return {
        "instrument_id": str(instrument_id),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "book": snapshot,
    }


@router.get("/order-books")
async def get_all_order_books() -> dict[str, Any]:
    """Returns the current state of all order books."""
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "order_books": order_book_cache.get_all_order_books(),
    }


@router.get("/instruments")
async def get_active_instruments() -> dict[str, Any]:
    """Returns a list of all instrument IDs that have an active order book."""
    order_books = order_book_cache.get_all_order_books()
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "instrument_ids": sorted(order_books.keys()),
    }
