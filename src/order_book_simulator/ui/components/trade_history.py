from typing import Callable

import pandas as pd
import streamlit as st

from order_book_simulator.ui.components.api_client import (
    get_all_trades,
    get_trades_for_stock,
)
from order_book_simulator.ui.components.utils import format_timestamp


def display_trade_history():
    """Display trade history with filtering options."""
    st.header("Trade History")

    # Filter controls
    col1, col2, col3 = st.columns(3)

    with col1:
        view_mode = st.selectbox(
            "View Mode", ["All Stocks", "Single Stock"], key="trade_view_mode"
        )

    with col2:
        limit = st.number_input(
            "Number of Trades to Display",
            min_value=10,
            value=100,
            step=10,
            key="trade_limit",
        )

    with col3:
        if view_mode == "Single Stock":
            # Get available stocks for selection
            available_stocks = st.session_state.get(
                "available_stocks", ["AAPL", "MSFT", "GOOGL"]
            )
            selected_stock = st.selectbox(
                "Select Stock", available_stocks, key="trade_stock_filter"
            )
        else:
            selected_stock = None

    # Fetch and display trades
    try:
        if view_mode == "All Stocks":
            trades_data = get_all_trades(limit=limit)
        else:
            if selected_stock:
                trades_data = get_trades_for_stock(selected_stock, limit=limit)
            else:
                st.error("Please select a stock")
                return

        if trades_data and "trades" in trades_data:
            trades = trades_data["trades"]

            if trades:
                # Convert to DataFrame for display
                df = pd.DataFrame(trades)

                # Format the DataFrame and sort by most recent first
                if "trade_time" in df.columns:
                    df["trade_time"] = pd.to_datetime(df["trade_time"])
                    df = df.sort_values(by="trade_time", ascending=False)
                    df["Time"] = df["trade_time"].dt.strftime("%H:%M:%S")
                    df["Date"] = df["trade_time"].dt.strftime("%Y-%m-%d")

                # Select and rename columns for display
                display_columns = []
                column_mapping = {}

                if "ticker" in df.columns:
                    display_columns.append("ticker")
                    column_mapping["ticker"] = "Ticker"

                if "Date" in df.columns:
                    display_columns.append("Date")

                if "Time" in df.columns:
                    display_columns.append("Time")

                if "price" in df.columns:
                    display_columns.append("price")
                    column_mapping["price"] = "Price ($)"

                if "quantity" in df.columns:
                    display_columns.append("quantity")
                    column_mapping["quantity"] = "Quantity"

                if "total_amount" in df.columns:
                    display_columns.append("total_amount")
                    column_mapping["total_amount"] = "Total ($)"

                # Get actual total trades from API for overall context
                actual_total_trades = len(trades)  # Default fallback

                if view_mode == "All Stocks":
                    # Get global trade count from API
                    from order_book_simulator.ui.components.api_client import (
                        get_global_trade_analytics,
                    )

                    analytics_data = get_global_trade_analytics(since_hours=8760)
                    if analytics_data and "analytics" in analytics_data:
                        actual_total_trades = analytics_data["analytics"].get(
                            "trade_count", len(trades)
                        )
                elif view_mode == "Single Stock" and selected_stock:
                    # Get stock-specific trade count from API
                    from order_book_simulator.ui.components.api_client import (
                        get_trade_analytics,
                    )

                    analytics_data = get_trade_analytics(
                        selected_stock, since_hours=8760
                    )
                    if analytics_data and "analytics" in analytics_data:
                        actual_total_trades = analytics_data["analytics"].get(
                            "trade_count", len(trades)
                        )

                # Create display DataFrame
                display_df = df[display_columns].copy()
                display_df = display_df.rename(columns=column_mapping)

                # Display summary metrics
                st.subheader("Trade Summary")

                # Total trades in its own row
                st.metric("Total Trades", f"{actual_total_trades:,}")

                # Trade metrics for displayed trades
                st.subheader(f"Trade Metrics (Last {len(trades)} Trades)")

                col1, col2, col3, col4 = st.columns(4)

                # Calculate price analytics from displayed trades
                prices = [float(trade["price"]) for trade in trades] if trades else []
                quantities = (
                    [float(trade["quantity"]) for trade in trades] if trades else []
                )
                total_amounts = (
                    [float(trade["total_amount"]) for trade in trades] if trades else []
                )

                # Calculate metrics
                avg_price = sum(prices) / len(prices) if prices else None
                min_price = min(prices) if prices else None
                max_price = max(prices) if prices else None

                # Calculate VWAP: total_value / total_volume
                total_volume = sum(quantities)
                total_value = sum(total_amounts)
                vwap = total_value / total_volume if total_volume > 0 else None

                with col1:
                    st.metric("Total Volume", f"{total_volume:,.2f}")

                with col2:
                    st.metric("Total Value", f"${total_value:,.2f}")

                with col3:
                    if trades:
                        avg_quantity = total_volume / len(trades)
                        st.metric("Average Quantity", f"{avg_quantity:.2f}")
                    else:
                        st.metric("Average Quantity", "N/A")

                with col4:
                    if vwap is not None:
                        st.metric("VWAP", f"${vwap:.2f}")
                    else:
                        st.metric("VWAP", "N/A")

                # Second row for price analytics
                col1, col2, col3, col4 = st.columns(4)

                with col1:
                    if avg_price is not None:
                        st.metric("Average Price", f"${avg_price:.2f}")
                    else:
                        st.metric("Average Price", "N/A")

                with col2:
                    if min_price is not None:
                        st.metric("Min Price", f"${min_price:.2f}")
                    else:
                        st.metric("Min Price", "N/A")

                with col3:
                    if max_price is not None:
                        st.metric("Max Price", f"${max_price:.2f}")
                    else:
                        st.metric("Max Price", "N/A")

                with col4:
                    # Empty column for balance
                    st.empty()

                # Display trades table
                st.subheader("Recent Trades")
                st.dataframe(
                    display_df,
                    use_container_width=True,
                    column_config={
                        "Price ($)": st.column_config.NumberColumn(
                            "Price ($)", format="$%.2f"
                        ),
                        "Quantity": st.column_config.NumberColumn(
                            "Quantity", format="%.2f"
                        ),
                        "Total ($)": st.column_config.NumberColumn(
                            "Total ($)", format="$%.2f"
                        ),
                    },
                )

                # Display timestamp of last update
                if "timestamp" in trades_data:
                    last_update = format_timestamp(trades_data["timestamp"])
                    st.caption(f"Last updated: {last_update}")

            else:
                st.info("No trades found for the selected criteria.")
        else:
            st.error("Failed to fetch trade data.")

    except Exception as e:
        st.error(f"Error fetching trade data: {str(e)}")


def create_auto_refresh_trade_history(interval: int) -> Callable:
    """
    Creates an auto-refreshing trade history fragment with configurable
    interval.

    Args:
        interval: The interval in seconds at which to refresh the trade
                  history.

    Returns:
        The auto-refreshing trade history fragment.
    """

    @st.fragment(run_every=interval)
    def auto_refresh_trade_history():
        display_trade_history()

    return auto_refresh_trade_history
