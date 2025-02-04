from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from order_book_simulator.common.models import OrderSide, OrderType
from order_book_simulator.matching.order_book import OrderBook


def create_order(
    price: Decimal | None = None,
    quantity: Decimal = Decimal("1.0"),
    side: OrderSide = OrderSide.BUY,
    order_type: OrderType = OrderType.LIMIT,
) -> dict:
    """Creates an order dictionary for testing."""
    return {
        "id": uuid4(),
        "price": price,
        "quantity": quantity,
        "side": side,
        "type": order_type,
        "created_at": datetime.now(timezone.utc),
    }


def test_add_buy_limit_order_to_empty_book(order_book: OrderBook) -> None:
    """Tests adding a buy limit order to an empty order book."""
    order = create_order(price=Decimal("100"), quantity=Decimal("10"))
    trades = order_book.add_order(order)

    assert not trades
    assert len(order_book.bids) == 1
    assert len(order_book._bid_orders) == 1
    assert order_book.bids[Decimal("100")].quantity == Decimal("10")
    assert order_book.bids[Decimal("100")].order_count == 1


def test_add_sell_limit_order_to_empty_book(order_book: OrderBook) -> None:
    """Tests adding a sell limit order to an empty order book."""
    order = create_order(
        price=Decimal("100"), quantity=Decimal("10"), side=OrderSide.SELL
    )
    trades = order_book.add_order(order)

    assert not trades
    assert len(order_book.asks) == 1
    assert len(order_book._ask_orders) == 1
    assert order_book.asks[Decimal("100")].quantity == Decimal("10")
    assert order_book.asks[Decimal("100")].order_count == 1


def test_matching_limit_orders(order_book: OrderBook) -> None:
    """Tests matching of limit orders at the same price level."""
    sell_order = create_order(
        price=Decimal("100"),
        quantity=Decimal("10"),
        side=OrderSide.SELL,
    )
    order_book.add_order(sell_order)

    buy_order = create_order(price=Decimal("100"), quantity=Decimal("5"))
    trades = order_book.add_order(buy_order)

    assert len(trades) == 1
    assert trades[0]["price"] == Decimal("100")
    assert trades[0]["quantity"] == Decimal("5")
    assert trades[0]["buyer_order_id"] == buy_order["id"]
    assert trades[0]["seller_order_id"] == sell_order["id"]

    assert len(order_book.asks) == 1
    assert order_book.asks[Decimal("100")].quantity == Decimal("5")


def test_price_time_priority_matching(order_book: OrderBook) -> None:
    """Tests that orders match according to price-time priority."""
    # Add two sell orders at different prices
    first_sell = create_order(
        price=Decimal("100"),
        quantity=Decimal("5"),
        side=OrderSide.SELL,
    )
    second_sell = create_order(
        price=Decimal("101"),
        quantity=Decimal("5"),
        side=OrderSide.SELL,
    )
    order_book.add_order(first_sell)
    order_book.add_order(second_sell)

    # Add a buy order that could match either sell.
    buy_order = create_order(price=Decimal("101"), quantity=Decimal("5"))
    trades = order_book.add_order(buy_order)

    assert len(trades) == 1
    # Should match the better price.
    assert trades[0]["price"] == Decimal("100")
    # Shouild match the first order.
    assert trades[0]["seller_order_id"] == first_sell["id"]


def test_market_order_matching(order_book: OrderBook) -> None:
    """Tests matching of market orders against limit orders."""
    sell_order = create_order(
        price=Decimal("100"),
        quantity=Decimal("10"),
        side=OrderSide.SELL,
    )
    order_book.add_order(sell_order)

    market_buy = create_order(
        price=None,
        quantity=Decimal("5"),
        order_type=OrderType.MARKET,
    )
    trades = order_book.add_order(market_buy)

    assert len(trades) == 1
    assert trades[0]["price"] == Decimal("100")
    assert trades[0]["quantity"] == Decimal("5")


def test_multiple_trades_from_single_order(order_book: OrderBook) -> None:
    """Tests an order matching against multiple resting orders."""
    # Add two sell orders at different prices.
    first_sell = create_order(
        price=Decimal("100"),
        quantity=Decimal("5"),
        side=OrderSide.SELL,
    )
    second_sell = create_order(
        price=Decimal("101"),
        quantity=Decimal("5"),
        side=OrderSide.SELL,
    )
    order_book.add_order(first_sell)
    order_book.add_order(second_sell)

    # Add a large buy order that should match both sells.
    buy_order = create_order(price=Decimal("101"), quantity=Decimal("10"))
    trades = order_book.add_order(buy_order)

    assert len(trades) == 2
    assert trades[0]["price"] == Decimal("100")
    assert trades[1]["price"] == Decimal("101")
    # The book should be empty.
    assert not order_book.asks


def test_market_order_with_price_raises_error(order_book: OrderBook) -> None:
    """Tests that market orders with a price specified raise an error."""
    sell_order = create_order(
        price=Decimal("100"),
        quantity=Decimal("10"),
        side=OrderSide.SELL,
    )
    order_book.add_order(sell_order)
    market_buy = create_order(
        price=Decimal("100"),
        quantity=Decimal("5"),
        order_type=OrderType.MARKET,
    )

    # Prices shouldn't be provided for market orders.
    with pytest.raises(ValueError, match="Market orders should not have a price"):
        order_book.add_order(market_buy)
