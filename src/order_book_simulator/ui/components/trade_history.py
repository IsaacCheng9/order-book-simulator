import pandas as pd
import streamlit as st

from order_book_simulator.ui.components.api_client import (
    get_all_trades,
    get_global_trade_analytics,
    get_trade_analytics,
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
            "Number of Trades",
            min_value=10,
            max_value=1000,
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

                # Create display DataFrame
                display_df = df[display_columns].copy()
                display_df = display_df.rename(columns=column_mapping)

                # Display summary metrics
                st.subheader("Trade Summary")

                col1, col2, col3, col4 = st.columns(4)

                # Get actual total trades count
                actual_total_trades = len(trades)  # Default to displayed count
                if view_mode == "All Stocks":
                    # Use global analytics for total trades across all stocks
                    # over the last year (8760 hours)
                    global_analytics_data = get_global_trade_analytics(since_hours=8760)
                    if global_analytics_data and "analytics" in global_analytics_data:
                        actual_total_trades = global_analytics_data["analytics"].get(
                            "trade_count", len(trades)
                        )
                elif view_mode == "Single Stock" and selected_stock:
                    # Try to get actual total from analytics
                    analytics_data = get_trade_analytics(
                        selected_stock, since_hours=8760
                    )  # 1 year
                    if analytics_data and "analytics" in analytics_data:
                        actual_total_trades = analytics_data["analytics"].get(
                            "trade_count", len(trades)
                        )

                with col1:
                    st.metric("Total Trades", f"{actual_total_trades:,}")

                with col2:
                    total_volume = sum(float(trade["quantity"]) for trade in trades)
                    st.metric("Total Volume", f"{total_volume:,.2f}")

                with col3:
                    total_value = sum(float(trade["total_amount"]) for trade in trades)
                    st.metric("Total Value", f"${total_value:,.2f}")

                with col4:
                    if trades:
                        avg_quantity = sum(
                            float(trade["quantity"]) for trade in trades
                        ) / len(trades)
                        st.metric("Average Quantity", f"{avg_quantity:.2f}")

                # Display trades table
                st.subheader("Recent Trades")
                st.dataframe(
                    display_df,
                    use_container_width=True,
                    hide_index=True,
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


@st.fragment(run_every=3)
def create_auto_refresh_trade_history():
    """Auto-refreshing trade history component."""
    display_trade_history()
