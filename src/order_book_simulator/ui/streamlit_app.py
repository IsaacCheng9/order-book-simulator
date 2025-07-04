from datetime import datetime, timezone

import streamlit as st

from order_book_simulator.ui.components.api_client import (
    check_gateway_connection,
    get_all_stocks,
)
from order_book_simulator.ui.components.market_overview import (
    create_auto_refresh_market_overview,
    create_market_overview,
)
from order_book_simulator.ui.components.order_book import (
    create_auto_refresh_order_book,
    display_individual_order_book,
)
from order_book_simulator.ui.components.order_form import create_order_form
from order_book_simulator.ui.components.trade_history import (
    create_auto_refresh_trade_history,
    display_trade_history,
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
    st.title("Order Book Simulator")
    st.sidebar.header("Controls")

    # Connection status indicator
    gateway_connected = check_gateway_connection()
    if gateway_connected:
        st.sidebar.success("✅ Gateway Connected")
    else:
        st.sidebar.error("❌ Gateway Disconnected")

    # View mode selection - defaults to market overview
    navigation = st.sidebar.radio(
        "Navigation",
        [
            "Market Overview",
            "Individual Order Book",
            "Trade History",
            "Submit Order",
        ],
    )

    # Store available stocks in session state for components that need them
    stocks_data = get_all_stocks()
    if stocks_data and stocks_data.get("stocks"):
        stock_options = [stock["ticker"] for stock in stocks_data["stocks"]]
        st.session_state.available_stocks = stock_options
    else:
        # Fallback to hardcoded list if API is unavailable
        st.session_state.available_stocks = [
            "AAPL",
            "AMZN",
            "GOOGL",
            "META",
            "MSFT",
            "NVDA",
            "TSLA",
        ]

    # Auto-refresh controls
    st.sidebar.subheader("Auto-Refresh")
    auto_refresh_enabled = st.sidebar.checkbox("Enable Auto-Refresh", value=True)

    # Add selection for the refresh interval if the auto-refresh is enabled
    if auto_refresh_enabled:
        refresh_interval = st.sidebar.selectbox(
            "Refresh Interval",
            [1, 2, 3, 5, 10],
            index=2,  # Default to 3 seconds
            format_func=lambda x: f"{x} second{'s' if x != 1 else ''}",
        )
        st.session_state.refresh_interval = refresh_interval
    else:
        refresh_interval = 3  # Default fallback
    st.session_state.auto_refresh_enabled = auto_refresh_enabled

    # Last update time
    st.sidebar.info(
        f"Last Updated: {datetime.now(timezone.utc).strftime('%H:%M:%S')} UTC"
    )

    # Main content area - conditional on view mode with auto-refresh
    if navigation == "Market Overview":
        if auto_refresh_enabled:
            auto_refresh_fragment = create_auto_refresh_market_overview(
                refresh_interval
            )
            auto_refresh_fragment()
        else:
            create_market_overview()
    elif navigation == "Individual Order Book":
        if auto_refresh_enabled:
            auto_refresh_fragment = create_auto_refresh_order_book(refresh_interval)
            auto_refresh_fragment()
        else:
            display_individual_order_book()
    elif navigation == "Trade History":
        if gateway_connected:
            if auto_refresh_enabled:
                auto_refresh_fragment = create_auto_refresh_trade_history(
                    refresh_interval
                )
                auto_refresh_fragment()
            else:
                display_trade_history()
        else:
            st.error("Gateway connection required to view trade history")
    elif navigation == "Submit Order":
        if gateway_connected:
            create_order_form()
        else:
            st.error("Gateway connection required to submit orders")


if __name__ == "__main__":
    main()
