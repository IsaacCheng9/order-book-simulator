from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from order_book_simulator.common.models import OrderRequest, OrderType
from order_book_simulator.database.models import Stock
from order_book_simulator.database.queries import get_stock_by_ticker


async def validate_order(order_request: OrderRequest, db: AsyncSession) -> Stock:
    """
    Validates an equity order request against business rules and current market
    conditions.

    Args:
        order_request: The order request to validate.
        db: Database session.

    Raises:
        HTTPException if validation fails.

    Returns:
        The validated Stock object.
    """
    # Look up stock by ticker
    stock = await get_stock_by_ticker(order_request.ticker, db)
    if not stock:
        raise HTTPException(
            status_code=404, detail=f"Stock ticker {order_request.ticker} not found"
        )

    # Validate order size (must be whole number of shares)
    if not float(order_request.quantity).is_integer():
        raise HTTPException(
            status_code=400,
            detail="Order quantity must be a whole number of shares",
        )

    if (
        order_request.quantity < stock.min_order_size
        or order_request.quantity > stock.max_order_size
    ):
        raise HTTPException(
            status_code=400,
            detail=(
                f"Order quantity must be between {stock.min_order_size} and "
                f"{stock.max_order_size} shares"
            ),
        )

    # Validate price for limit orders.
    if order_request.type == OrderType.LIMIT and not order_request.price:
        raise HTTPException(status_code=400, detail="Limit orders must specify a price")

    return stock
