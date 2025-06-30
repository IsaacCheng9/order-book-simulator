import pandas as pd
import streamlit as st

from order_book_simulator.ui.components.api_client import get_order_book_data
from order_book_simulator.ui.components.utils import format_timestamp


def display_single_stock_order_book(ticker: str) -> None:
    """
    Displays the order book for a single stock.

    Args:
        ticker: The stock ticker to display
    """
    st.subheader(f"Order Book for {ticker}")

    # Fetch real order book data
    order_book_data = get_order_book_data(ticker)

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
        st.info(f"No order book data available for {ticker}")
