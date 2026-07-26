from decimal import Decimal
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from order_book_simulator.common.models import OrderRequest, OrderSide, OrderType
from order_book_simulator.gateway import validation as validation_module
from order_book_simulator.gateway.validation import validate_order


def _stock(price_precision: int = 2) -> SimpleNamespace:
    """Create a stock-like object for validation tests."""
    return SimpleNamespace(
        id=uuid4(),
        ticker="AAPL",
        min_order_size=Decimal(1),
        max_order_size=Decimal(1000),
        price_precision=price_precision,
    )


def _order(
    order_type: OrderType = OrderType.LIMIT,
    price: Decimal | None = Decimal("100.00"),
    quantity: Decimal = Decimal(10),
) -> OrderRequest:
    """Create a valid base order request for validation tests."""
    return OrderRequest(
        user_id=uuid4(),
        ticker="AAPL",
        type=order_type,
        side=OrderSide.BUY,
        price=price,
        quantity=quantity,
    )


def _db() -> AsyncSession:
    """Create a typed mock database session."""
    return cast(AsyncSession, AsyncMock(spec=AsyncSession))


@pytest.fixture
def stock_lookup(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Patch stock lookup to return a known stock."""
    stock = _stock()

    async def mock_get_stock_by_ticker(ticker, db):
        return stock

    monkeypatch.setattr(
        validation_module,
        "get_stock_by_ticker",
        mock_get_stock_by_ticker,
    )
    return stock


@pytest.mark.asyncio
async def test_validate_order_accepts_valid_limit_order(stock_lookup):
    """Validates a correctly formed limit order."""
    stock = await validate_order(_order(), db=_db())

    assert stock is stock_lookup


@pytest.mark.asyncio
async def test_validate_order_accepts_valid_market_order(stock_lookup):
    """Validates a correctly formed market order."""
    stock = await validate_order(_order(OrderType.MARKET, price=None), db=_db())

    assert stock is stock_lookup


@pytest.mark.asyncio
async def test_validate_order_rejects_stop_orders(stock_lookup):
    """Rejects stop orders until stop-trigger state exists."""
    with pytest.raises(HTTPException) as exc_info:
        await validate_order(_order(OrderType.STOP), db=_db())

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "STOP orders are not supported"


@pytest.mark.asyncio
async def test_validate_order_rejects_market_orders_with_price(stock_lookup):
    """Rejects market orders that incorrectly provide a price."""
    with pytest.raises(HTTPException) as exc_info:
        await validate_order(_order(OrderType.MARKET, price=Decimal(100)), db=_db())

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Market orders must not specify a price"


@pytest.mark.asyncio
async def test_validate_order_rejects_limit_orders_without_price(stock_lookup):
    """Rejects limit orders that do not provide a limit price."""
    with pytest.raises(HTTPException) as exc_info:
        await validate_order(_order(price=None), db=_db())

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Limit orders must specify a price"


@pytest.mark.asyncio
@pytest.mark.parametrize("price", [Decimal(0), Decimal(-1)])
async def test_validate_order_rejects_non_positive_limit_price(
    stock_lookup,
    price: Decimal,
):
    """Rejects zero and negative limit prices."""
    with pytest.raises(HTTPException) as exc_info:
        await validate_order(_order(price=price), db=_db())

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Limit order price must be positive"


@pytest.mark.asyncio
async def test_validate_order_rejects_invalid_price_precision(stock_lookup):
    """Rejects prices outside the stock's configured tick size."""
    stock_lookup.price_precision = 2

    with pytest.raises(HTTPException) as exc_info:
        await validate_order(_order(price=Decimal("100.001")), db=_db())

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == (
        "Limit order price must use at most 2 decimal places"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("quantity", [Decimal(0), Decimal(-1)])
async def test_validate_order_rejects_non_positive_quantity(
    stock_lookup,
    quantity: Decimal,
):
    """Rejects zero and negative quantities."""
    with pytest.raises(HTTPException) as exc_info:
        await validate_order(_order(quantity=quantity), db=_db())

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Order quantity must be positive"
