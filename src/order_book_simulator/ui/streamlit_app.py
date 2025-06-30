from datetime import datetime

import pandas as pd
import requests
import streamlit as st


def check_gateway_connection() -> bool:
    """
    Checks the connection to the gateway service.

    Returns:
        True if the connection is successful, otherwise False.
    """
    try:
        # The Streamlit Docker service is configured to use the gateway
        # service, which is why we're using gateway as the hostname instead of
        # localhost.
        response = requests.get("http://gateway:8000/v1/health", timeout=2)
        return response.status_code == 200
    except Exception as e:
        st.error(f"Error checking gateway connection: {e}")
        return False


def get_order_book_data(ticker: str) -> dict | None:
    """
    Fetches order book data for a specific ticker.

    Args:
        ticker: The stock ticker to fetch data for

    Returns:
        Order book data or None if unavailable
    """
    try:
        response = requests.get(
            f"http://gateway:8000/v1/order-book/{ticker}", timeout=5
        )
        if response.status_code == 200:
            return response.json()
        elif response.status_code == 404:
            st.warning(f"No order book data found for {ticker}")
            return None
        else:
            st.error(f"Failed to fetch order book: {response.status_code}")
            return None
    except Exception as e:
        st.error(f"Error fetching order book: {e}")
        return None


def main():
    """
    Orchestrates the Streamlit app flow.
    """
    st.set_page_config(
        page_title="Order Book Simulator",
        page_icon="📈",
        layout="wide",
    )

    # Main header
    st.title("Order Book Simulator")
    st.text(
        "This is the dashboard for the Order Book Simulator, providing a visual representation of the various components of the system."
    )

    # Sidebar for controls
    st.sidebar.header("Controls")

    # Connection status indicator
    gateway_connected = check_gateway_connection()
    if gateway_connected:
        st.sidebar.success("✅ Gateway Connected")
    else:
        st.sidebar.error("❌ Gateway Disconnected")

    # Stock selection
    ticker = st.sidebar.selectbox(
        "Select Stock",
        ["AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA"],
    )

    # Last update time
    st.sidebar.info(f"Last Updated: {datetime.now().strftime('%H:%M:%S')}")

    # Main content area - Order book display
    if ticker:
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
                            "price": "Price",
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
                            "price": "Price",
                            "quantity": "Quantity",
                            "order_count": "Order Count",
                        }
                    )
                    st.dataframe(asks_df, use_container_width=True)
                else:
                    st.info("No asks available")

            # Show last updated timestamp
            if order_book_data.get("timestamp"):
                st.caption(f"Last updated: {order_book_data['timestamp']}")
        else:
            st.info(f"No order book data available for {ticker}")


if __name__ == "__main__":
    main()
