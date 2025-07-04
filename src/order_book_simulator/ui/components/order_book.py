from typing import Callable

import pandas as pd
import streamlit as st

from order_book_simulator.ui.components.api_client import get_order_book_data
from order_book_simulator.ui.components.utils import format_timestamp


def display_individual_order_book() -> None:
    """
    Displays the order book for a single stock with stock selection controls.
    """
    st.header("Individual Order Book View")

    # Stock selection controls
    available_stocks = st.session_state.get("available_stocks", [])
    if not available_stocks:
        st.error("No stocks available. Please check the gateway connection.")
        return

    # Stock selection dropdown
    selected_stock = st.selectbox(
        "Select Stock",
        available_stocks,
        key="orderbook_stock_selection",
        width=150,
    )

    if not selected_stock:
        st.info("Please select a stock to view its order book.")
        return

    st.subheader(f"Order Book for {selected_stock}")

    # Fetch real order book data
    order_book_data = get_order_book_data(selected_stock)

    if order_book_data and order_book_data.get("book"):
        book = order_book_data["book"]

        # Create two columns for bids and asks
        bids_col, asks_col = st.columns(2)

        with bids_col:
            st.write("### Bids")
            if book.get("bids"):
                # Convert to DataFrame for better display
                bids_df = pd.DataFrame(book["bids"]).rename(
                    columns={
                        "price": "Price ($)",
                        "quantity": "Quantity",
                        "order_count": "Order Count",
                    }
                )
                st.dataframe(bids_df, use_container_width=True)
            else:
                st.info("No bids available")

        with asks_col:
            st.write("### Asks")
            if book.get("asks"):
                asks_df = pd.DataFrame(book["asks"]).rename(
                    columns={
                        "price": "Price ($)",
                        "quantity": "Quantity",
                        "order_count": "Order Count",
                    }
                )
                st.dataframe(asks_df, use_container_width=True)
            else:
                st.info("No asks available")

        # Show last updated timestamp
        if order_book_data.get("timestamp"):
            formatted_time = format_timestamp(order_book_data["timestamp"])
            st.caption(f"Last Updated: {formatted_time}")
    else:
        st.info(f"No order book data available for {selected_stock}")


def create_auto_refresh_order_book(interval: int) -> Callable:
    """
    Creates an auto-refreshing order book fragment with configurable interval.

    Args:
        interval: The interval in seconds at which to refresh the order book.

    Returns:
        The auto-refreshing order book Streamlit fragment.
    """

    @st.fragment(run_every=interval)
    def auto_refresh_order_book():
        display_individual_order_book()

    return auto_refresh_order_book
