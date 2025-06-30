import os

import requests
import streamlit as st

# Get gateway URL from environment variable
GATEWAY_URL = os.getenv("GATEWAY_URL", "http://gateway:8000")


def check_gateway_connection() -> bool:
    """
    Checks the connection to the gateway service.

    Returns:
        True if the connection is successful, otherwise False.
    """
    try:
        response = requests.get(f"{GATEWAY_URL}/v1/health", timeout=2)
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
        response = requests.get(f"{GATEWAY_URL}/v1/order-book/{ticker}", timeout=5)
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


def get_all_order_books() -> dict | None:
    """
    Fetches all order book data from the collections endpoint.

    Returns:
        Dictionary containing all order books or None if unavailable
    """
    try:
        response = requests.get(f"{GATEWAY_URL}/v1/order-book/collection", timeout=5)
        if response.status_code == 200:
            return response.json()
        else:
            st.error(f"Failed to fetch order book collection: {response.status_code}")
            return None
    except Exception as e:
        st.error(f"Error fetching order book collection: {e}")
        return None


def get_active_stock_tickers() -> dict | None:
    """
    Fetches active stock tickers from the market data endpoint.

    Returns:
        Dictionary containing active stock tickers or None if unavailable
    """
    try:
        response = requests.get(
            f"{GATEWAY_URL}/v1/market-data/stocks-with-orders", timeout=5
        )
        if response.status_code == 200:
            return response.json()
        else:
            st.error(f"Failed to fetch stock tickers: {response.status_code}")
            return None
    except Exception as e:
        st.error(f"Error fetching stock tickers: {e}")
        return None
