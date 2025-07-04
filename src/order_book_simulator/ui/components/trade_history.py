from datetime import datetime
from typing import Callable

import pandas as pd
import streamlit as st

from order_book_simulator.ui.components.api_client import (
    get_all_trades,
    get_global_trade_analytics,
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

                # Always get global market data for Trading Activity Summary
                global_analytics_data = get_global_trade_analytics(since_hours=8760)
                actual_total_trades = len(trades)  # Default fallback

                if global_analytics_data and "analytics" in global_analytics_data:
                    actual_total_trades = global_analytics_data["analytics"].get(
                        "trade_count", len(trades)
                    )

                # For activity rate calculation, get all trades (not just
                # displayed)
                all_trades_data = get_all_trades(limit=1000)
                all_trades = (
                    all_trades_data.get("trades", []) if all_trades_data else []
                )

                # Create display DataFrame
                display_df = df[display_columns].copy()
                display_df = display_df.rename(columns=column_mapping)

                # Calculate metrics using trades for all stocks - this is
                # separate to what the user has selected for the display
                st.subheader("Trading Activity Summary")
                active_stocks = len(
                    set(trade["ticker"] for trade in all_trades if "ticker" in trade)
                )

                # Calculate market activity rate (trades per minute) using all
                # trades
                if all_trades and len(all_trades) > 1:
                    first_trade_time = datetime.fromisoformat(
                        all_trades[-1]["trade_time"].replace("Z", "+00:00")
                    )
                    last_trade_time = datetime.fromisoformat(
                        all_trades[0]["trade_time"].replace("Z", "+00:00")
                    )
                    time_diff_minutes = (
                        last_trade_time - first_trade_time
                    ).total_seconds() / 60
                    activity_rate = (
                        len(all_trades) / time_diff_minutes
                        if time_diff_minutes > 0
                        else 0
                    )
                else:
                    activity_rate = 0

                # Trade summary metrics in 3x1 grid
                col1, col2, col3 = st.columns(3)
                with col1:
                    st.metric("Total Trades", f"{actual_total_trades:,}")
                with col2:
                    st.metric("Active Stocks", f"{active_stocks:,}")
                with col3:
                    st.metric("Market Activity", f"{activity_rate:.1f} trades / min")

                # Trade metrics for displayed trades
                st.subheader(
                    f"Trade Metrics – Last {len(trades)} Trades for "
                    f"{selected_stock if selected_stock else 'All Stocks'}"
                )
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
