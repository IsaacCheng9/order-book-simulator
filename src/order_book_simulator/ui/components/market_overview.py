from typing import Callable

import pandas as pd
import streamlit as st

from order_book_simulator.ui.components.api_client import (
    get_active_stock_tickers,
    get_all_order_books,
)
from order_book_simulator.ui.components.utils import format_timestamp


def create_market_overview() -> None:
    """
    Creates a market overview table showing best bid/ask for all stocks.
    """
    st.subheader("Market Overview")

    # Get all order books and stock tickers
    all_order_books = get_all_order_books()
    stock_tickers_data = get_active_stock_tickers()

    if not all_order_books or not all_order_books.get("order_books"):
        st.info("No order book data available")
        return

    # Use the stock_id -> ticker mapping from the API
    stock_id_to_ticker = {}
    if stock_tickers_data and stock_tickers_data.get("stock_id_to_ticker"):
        stock_id_to_ticker = stock_tickers_data["stock_id_to_ticker"]

    # Create market overview data
    overview_data = []

    for stock_id, book_data in all_order_books["order_books"].items():
        bids = book_data.get("bids", [])
        asks = book_data.get("asks", [])

        # Get best bid (highest price) and best ask (lowest price)
        best_bid = max(bids, key=lambda x: float(x["price"])) if bids else None
        best_ask = min(asks, key=lambda x: float(x["price"])) if asks else None

        # Calculate spread
        spread = None
        spread_pct = None
        if best_bid and best_ask:
            bid_price = float(best_bid["price"])
            ask_price = float(best_ask["price"])
            spread = ask_price - bid_price
            spread_pct = (spread / bid_price) * 100 if bid_price > 0 else 0

        # Use ticker if available, otherwise fall back to truncated stock ID
        display_name = stock_id_to_ticker.get(stock_id, stock_id[:8] + "...")

        overview_data.append(
            {
                "Ticker": display_name,
                "Best Bid ($)": f"{float(best_bid['price']):.2f}" if best_bid else "-",
                "Best Ask ($)": f"{float(best_ask['price']):.2f}" if best_ask else "-",
                "Spread ($)": f"${spread:.2f}" if spread is not None else "-",
                "Spread (%)": f"{spread_pct:.2f}%" if spread_pct is not None else "-",
                "Bid Size": int(float(best_bid["quantity"])) if best_bid else 0,
                "Ask Size": int(float(best_ask["quantity"])) if best_ask else 0,
            }
        )

    if overview_data:
        df = pd.DataFrame(overview_data)
        st.dataframe(df, use_container_width=True)

        # Show last update time
        if all_order_books.get("timestamp"):
            formatted_time = format_timestamp(all_order_books["timestamp"])
            st.caption(f"Last Updated: {formatted_time}")
    else:
        st.info("No active order books found")


def create_auto_refresh_market_overview(interval: int) -> Callable:
    """
    Creates an auto-refreshing market overview fragment with configurable
    interval.

    Args:
        interval: The interval in seconds at which to refresh the market
                  overview.

    Returns:
        The auto-refreshing market overview Streamlit fragment.
    """

    @st.fragment(run_every=interval)
    def auto_refresh_market_overview():
        create_market_overview()

    return auto_refresh_market_overview
