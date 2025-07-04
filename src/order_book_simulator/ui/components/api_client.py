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


def get_all_stocks() -> dict | None:
    """
    Fetches all stocks from the database.

    Returns:
        Dictionary containing all stocks or None if unavailable
    """
    try:
        response = requests.get(f"{GATEWAY_URL}/v1/market-data/stocks", timeout=5)
        if response.status_code == 200:
            return response.json()
        else:
            st.error(f"Failed to fetch stocks: {response.status_code}")
            return None
    except Exception as e:
        st.error(f"Error fetching stocks: {e}")
        return None


def submit_order(order_data: dict) -> dict | None:
    """
    Submits an order to the order book system.

    Args:
        order_data: Dictionary containing order information

    Returns:
        Order response dictionary or None if submission failed
    """
    try:
        response = requests.post(
            f"{GATEWAY_URL}/v1/order-book", json=order_data, timeout=10
        )
        if response.status_code == 200:
            return response.json()
        # Validation error
        elif response.status_code == 422:
            try:
                error_detail = response.json().get("detail", "Validation error")
            except ValueError:
                error_detail = f"Validation error (HTTP {response.status_code})"
            st.error(f"Order validation failed: {error_detail}")
            return None
        # Other non-200 status codes
        else:
            st.error(f"Failed to submit order: {response.status_code}")
            if response.content:
                try:
                    error_msg = response.json().get("detail", "Unknown error")
                    st.error(f"Error details: {error_msg}")
                except ValueError:
                    st.error(f"Error (HTTP {response.status_code})")
            return None
    except Exception as e:
        st.error(f"Error submitting order: {e}")
        return None


def get_all_trades(limit: int = 100) -> dict | None:
    """
    Fetches recent trades across all stocks.

    Args:
        limit: Maximum number of trades to return

    Returns:
        Dictionary containing trade data or None if unavailable
    """
    try:
        response = requests.get(
            f"{GATEWAY_URL}/v1/market-data/trades", params={"limit": limit}, timeout=5
        )
        if response.status_code == 200:
            return response.json()
        else:
            st.error(f"Failed to fetch trades: {response.status_code}")
            return None
    except Exception as e:
        st.error(f"Error fetching trades: {e}")
        return None


def get_trades_for_stock(ticker: str, limit: int = 100) -> dict | None:
    """
    Fetches recent trades for a specific stock.

    Args:
        ticker: Stock ticker symbol
        limit: Maximum number of trades to return

    Returns:
        Dictionary containing trade data or None if unavailable
    """
    try:
        response = requests.get(
            f"{GATEWAY_URL}/v1/market-data/trades/{ticker}",
            params={"limit": limit},
            timeout=5,
        )
        if response.status_code == 200:
            return response.json()
        elif response.status_code == 404:
            st.warning(f"No trades found for {ticker}")
            return None
        else:
            st.error(f"Failed to fetch trades for {ticker}: {response.status_code}")
            return None
    except Exception as e:
        st.error(f"Error fetching trades for {ticker}: {e}")
        return None


def get_trade_analytics(ticker: str, since_hours: int = 24) -> dict | None:
    """
    Fetches trade analytics for a specific stock.

    Args:
        ticker: Stock ticker symbol
        since_hours: Number of hours to look back for analytics

    Returns:
        Dictionary containing trade analytics or None if unavailable
    """
    try:
        response = requests.get(
            f"{GATEWAY_URL}/v1/market-data/trades/{ticker}/analytics",
            params={"since_hours": since_hours},
            timeout=5,
        )
        if response.status_code == 200:
            return response.json()
        elif response.status_code == 404:
            st.warning(f"No analytics found for {ticker}")
            return None
        else:
            st.error(f"Failed to fetch analytics for {ticker}: {response.status_code}")
            return None
    except Exception as e:
        st.error(f"Error fetching analytics for {ticker}: {e}")
        return None


def get_global_trade_analytics(since_hours: int = 24) -> dict | None:
    """
    Fetches global trade analytics across all stocks.

    Args:
        since_hours: Number of hours to look back for analytics

    Returns:
        Dictionary containing global trade analytics or None if unavailable
    """
    try:
        response = requests.get(
            f"{GATEWAY_URL}/v1/market-data/global-trades-analytics",
            params={"since_hours": since_hours},
            timeout=5,
        )
        if response.status_code == 200:
            return response.json()
        elif response.status_code == 404:
            st.warning("No global analytics found")
            return None
        else:
            st.error(f"Failed to fetch global analytics: {response.status_code}")
            return None
    except Exception as e:
        st.error(f"Error fetching global analytics: {e}")
        return None
