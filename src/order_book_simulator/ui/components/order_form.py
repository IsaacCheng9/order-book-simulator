from uuid import uuid4

import streamlit as st

from order_book_simulator.ui.components.api_client import (
    get_all_stocks,
    submit_order,
)


def create_order_form() -> None:
    """
    Creates a manual order submission form with validation.
    """
    st.subheader("Submit Order")

    # Get available stocks for the dropdown
    stocks_data = get_all_stocks()
    stock_options = []
    if stocks_data and stocks_data.get("stocks"):
        stock_options = [stock["ticker"] for stock in stocks_data["stocks"]]

    # Fallback to hardcoded options if API unavailable
    if not stock_options:
        stock_options = ["AAPL", "AMZN", "GOOGL", "META", "MSFT", "NVDA", "TSLA"]

    # Order type selection outside form for dynamic UI updates
    order_type = st.selectbox(
        "Order Type",
        ["LIMIT", "MARKET"],
        help="Limit orders specify a price, market orders execute immediately",
    )

    with st.form("order_form"):
        col1, col2 = st.columns(2)

        with col1:
            # Stock selection
            ticker = st.selectbox(
                "Stock", stock_options, help="Select the stock you want to trade"
            )

            # Order side
            side = st.selectbox(
                "Side", ["BUY", "SELL"], help="Choose whether to buy or sell"
            )

        with col2:
            # Quantity
            quantity = st.number_input(
                "Quantity",
                min_value=0.01,
                value=1.0,
                step=0.01,
                format="%.2f",
                help="Number of shares to trade",
            )

            # Price (only show for limit orders)
            price = None
            if order_type == "LIMIT":
                price = st.number_input(
                    "Price ($)",
                    min_value=0.01,
                    value=100.0,
                    step=0.01,
                    format="%.2f",
                    help="Price per share for limit orders",
                )

        # Persist the user ID in session state for consistency
        if "user_id" not in st.session_state:
            st.session_state.user_id = str(uuid4())
        # User ID at bottom left (simplified for demo)
        user_id = st.text_input(
            "User ID",
            value=st.session_state.user_id,
            help="Unique identifier for the user (persists for this session)",
        )

        # Submit button
        submitted = st.form_submit_button("Submit Order", type="primary")

        if submitted:
            # Validate inputs
            errors = []
            if not ticker:
                errors.append("Please select a stock")
            if quantity <= 0:
                errors.append("Quantity must be greater than 0")
            if order_type == "LIMIT" and (price is None or price <= 0):
                errors.append("Price must be greater than 0 for limit orders")
            if not user_id:
                errors.append("User ID is required")

            # Stop early if any validation failed
            if errors:
                for error in errors:
                    st.error(error)
                return

            # Submit the order
            order_data = {
                "user_id": user_id,
                "ticker": ticker,
                "type": order_type,
                "side": side,
                "quantity": str(quantity),
                "price": str(price) if price is not None else None,
            }
            with st.spinner("Submitting order..."):
                response = submit_order(order_data)
            if response:
                st.success(
                    "Order submitted successfully! "
                    f"Order ID: {response.get('id', 'N/A')}"
                )
                st.json(response)
            else:
                st.error("Failed to submit order. Please try again.")
