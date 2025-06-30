from datetime import datetime

import streamlit as st

from order_book_simulator.ui.components.api_client import check_gateway_connection
from order_book_simulator.ui.components.market_overview import create_market_overview
from order_book_simulator.ui.components.order_book import (
    display_single_stock_order_book,
)


def main():
    """
    Main entry point for the Streamlit Order Book Simulator application.
    """
    st.set_page_config(
        page_title="Order Book Simulator",
        page_icon="📈",
        layout="wide",
    )

    # Main header
    st.title("Order Book Simulator")
    st.text(
        "This is the dashboard for the Order Book Simulator, providing a visual "
        "representation of the various components of the system."
    )

    # Sidebar controls
    st.sidebar.header("Controls")

    # Connection status indicator
    gateway_connected = check_gateway_connection()
    if gateway_connected:
        st.sidebar.success("✅ Gateway Connected")
    else:
        st.sidebar.error("❌ Gateway Disconnected")

    # View mode selection - defaults to market overview
    view_mode = st.sidebar.radio(
        "View Mode",
        [
            "Market Overview",
            "Single Stock",
        ],
    )

    # Stock selection (only shown for single stock view)
    ticker = None
    if view_mode == "Single Stock":
        ticker = st.sidebar.selectbox(
            "Select Stock",
            ["AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA"],
        )

    # Last update time
    st.sidebar.info(f"Last Updated: {datetime.now().strftime('%H:%M:%S UTC')}")

    # Main content area - conditional on view mode
    if view_mode == "Market Overview":
        create_market_overview()
    elif view_mode == "Single Stock" and ticker:
        display_single_stock_order_book(ticker)


if __name__ == "__main__":
    main()
