from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from order_book_simulator.common.models import OrderRequest, OrderType
from order_book_simulator.database.db_models import Stock
from order_book_simulator.database.queries import get_stock_by_ticker

SUPPORTED_ORDER_TYPES = frozenset({OrderType.LIMIT, OrderType.MARKET})


def _has_valid_price_precision(price: Decimal, price_precision: int) -> bool:
    """Return whether a price is on the stock's configured tick size."""
    tick_size = Decimal(1).scaleb(-price_precision)
    return price == price.quantize(tick_size)


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
    # Look up stock by ticker.
    stock = await get_stock_by_ticker(order_request.ticker, db)
    if not stock:
        raise HTTPException(
            status_code=404, detail=f"Stock ticker {order_request.ticker} not found"
        )

    if order_request.type not in SUPPORTED_ORDER_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"{order_request.type.value} orders are not supported",
        )

    # Validate order size. US equity share quantities must be positive whole
    # numbers in this simulator.
    if not order_request.quantity.is_finite() or order_request.quantity <= Decimal(0):
        raise HTTPException(
            status_code=400,
            detail="Order quantity must be positive",
        )

    if order_request.quantity != order_request.quantity.to_integral_value():
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

    if order_request.type == OrderType.MARKET:
        if order_request.price is not None:
            raise HTTPException(
                status_code=400,
                detail="Market orders must not specify a price",
            )
        return stock

    # Validate price for limit orders.
    if order_request.price is None:
        raise HTTPException(status_code=400, detail="Limit orders must specify a price")

    if not order_request.price.is_finite() or order_request.price <= Decimal(0):
        raise HTTPException(
            status_code=400,
            detail="Limit order price must be positive",
        )

    if not _has_valid_price_precision(order_request.price, stock.price_precision):
        raise HTTPException(
            status_code=400,
            detail=(
                f"Limit order price must use at most {stock.price_precision} "
                "decimal places"
            ),
        )

    return stock
