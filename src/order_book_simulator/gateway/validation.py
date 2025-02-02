from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text
from order_book_simulator.common.models import OrderRequest, OrderType


async def validate_order(order_request: OrderRequest, db: AsyncSession) -> bool:
    """
    Validates an order request against business rules and current market
    conditions.

    Raises:
        HTTPException if validation fails.

    Returns:
        True if the order request is valid.
    """
    # Validate instrument exists.
    query = select(text("*")).select_from(text("instrument")).where(text("id = :id"))
    result = await db.execute(
        query,
        {"id": order_request.instrument_id},
    )
    instrument = result.first()
    if not instrument:
        raise HTTPException(status_code=404, detail="Instrument not found.")

    # Validate order size.
    if (
        order_request.quantity < instrument.min_order_size
        or order_request.quantity > instrument.max_order_size
    ):
        raise HTTPException(
            status_code=400,
            detail=(
                f"Order quantity must be between {instrument.min_order_size} and "
                f"{instrument.max_order_size}."
            ),
        )

    # Validate price for limit orders.
    if order_request.type == OrderType.LIMIT and not order_request.price:
        raise HTTPException(
            status_code=400, detail="Limit orders must specify a price."
        )

    # TODO: Add other validation logic here.
    return True
