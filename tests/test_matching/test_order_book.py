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
        "order_type": order_type,
        "created_at": datetime.now(timezone.utc),
    }


def test_add_buy_limit_order_to_empty_book(order_book: OrderBook) -> None:
    """Tests adding a buy limit order to an empty order book."""
    order = create_order(price=Decimal("100"), quantity=Decimal("10"))
    trades = order_book.add_order(order)

    assert not trades
    assert len(order_book.bid_levels) == 1
    assert order_book.bid_levels[Decimal("100")].quantity == Decimal("10")
    assert order_book.bid_levels[Decimal("100")].order_count == 1


def test_add_sell_limit_order_to_empty_book(order_book: OrderBook) -> None:
    """Tests adding a sell limit order to an empty order book."""
    order = create_order(
        price=Decimal("100"), quantity=Decimal("10"), side=OrderSide.SELL
    )
    trades = order_book.add_order(order)

    assert not trades
    assert len(order_book.ask_levels) == 1
    assert order_book.ask_levels[Decimal("100")].quantity == Decimal("10")
    assert order_book.ask_levels[Decimal("100")].order_count == 1


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

    assert len(order_book.ask_levels) == 1
    assert order_book.ask_levels[Decimal("100")].quantity == Decimal("5")


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
    assert not order_book.ask_levels


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


def test_cancel_order_success(order_book: OrderBook) -> None:
    """Tests successful order cancellation."""
    order = create_order(price=Decimal("100"), quantity=Decimal("10"))
    order_book.add_order(order)

    assert order["id"] in order_book.order_id_to_order
    assert Decimal("100") in order_book.bid_levels

    success = order_book.cancel_order(order["id"])

    assert success is True
    assert order["id"] not in order_book.order_id_to_order


def test_cancel_order_not_found(order_book: OrderBook) -> None:
    """Tests cancelling a non-existent order."""
    success = order_book.cancel_order(uuid4())
    assert success is False


def test_cancel_order_removes_empty_price_level(order_book: OrderBook) -> None:
    """Tests that empty price levels are removed after cancellation."""
    order = create_order(price=Decimal("100"), quantity=Decimal("10"))
    order_book.add_order(order)

    assert Decimal("100") in order_book.bid_levels

    order_book.cancel_order(order["id"])

    assert Decimal("100") not in order_book.bid_levels


def test_cancel_order_preserves_other_orders_at_same_price(
    order_book: OrderBook,
) -> None:
    """Tests that cancelling one order doesn't affect others at the same price."""
    order1 = create_order(price=Decimal("100"), quantity=Decimal("10"))
    order2 = create_order(price=Decimal("100"), quantity=Decimal("5"))
    order_book.add_order(order1)
    order_book.add_order(order2)

    assert order_book.bid_levels[Decimal("100")].quantity == Decimal("15")

    order_book.cancel_order(order1["id"])

    assert Decimal("100") in order_book.bid_levels
    assert order_book.bid_levels[Decimal("100")].quantity == Decimal("5")
    assert order2["id"] in order_book.order_id_to_order


def test_cancel_sell_order(order_book: OrderBook) -> None:
    """Tests cancelling a sell order."""
    order = create_order(
        price=Decimal("100"), quantity=Decimal("10"), side=OrderSide.SELL
    )
    order_book.add_order(order)

    assert Decimal("100") in order_book.ask_levels

    success = order_book.cancel_order(order["id"])

    assert success is True
    assert Decimal("100") not in order_book.ask_levels
    assert order["id"] not in order_book.order_id_to_order
