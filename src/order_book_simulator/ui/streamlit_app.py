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


def get_all_order_books() -> dict | None:
    """
    Fetches all order book data from the collections endpoint.

    Returns:
        Dictionary containing all order books or None if unavailable
    """
    try:
        response = requests.get(
            "http://gateway:8000/v1/order-book/collection", timeout=5
        )
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
            "http://gateway:8000/v1/market-data/stocks-with-orders", timeout=5
        )
        if response.status_code == 200:
            return response.json()
        else:
            st.error(f"Failed to fetch stock tickers: {response.status_code}")
            return None
    except Exception as e:
        st.error(f"Error fetching stock tickers: {e}")
        return None


def create_market_overview() -> None:
    """
    Creates a market overview table showing best bid/ask for all stocks.
    """
    st.subheader("Market Overview")

    # Get all order books and stock tickers
    all_order_books = get_all_order_books()
    stock_tickers_data = get_active_stock_tickers()

    if not all_order_books or not all_order_books.get("order_books"):
        st.info("No order book data available")
        return

    # Create a mapping from stock ID to ticker if available
    stock_id_to_ticker = {}
    if stock_tickers_data and stock_tickers_data.get("tickers"):
        # TODO: Enhance the API to return a direct stock_id -> ticker mapping
        tickers = stock_tickers_data["tickers"]
        stock_ids = list(all_order_books["order_books"].keys())

        # Simple heuristic: if we have the same number of tickers and stock
        # IDs, we can try to match them alphabetically for better performance
        if len(tickers) == len(stock_ids):
            for ticker, stock_id in zip(sorted(tickers), sorted(stock_ids)):
                stock_id_to_ticker[stock_id] = ticker

    # Create market overview data
    overview_data = []

    for stock_id, book_data in all_order_books["order_books"].items():
        bids = book_data.get("bids", [])
        asks = book_data.get("asks", [])

        # Get best bid (highest price) and best ask (lowest price)
        best_bid = max(bids, key=lambda x: float(x["price"])) if bids else None
        best_ask = min(asks, key=lambda x: float(x["price"])) if asks else None

        # Calculate spread
        spread = None
        spread_pct = None
        if best_bid and best_ask:
            bid_price = float(best_bid["price"])
            ask_price = float(best_ask["price"])
            spread = ask_price - bid_price
            spread_pct = (spread / bid_price) * 100 if bid_price > 0 else 0

        # Use ticker if available, otherwise fall back to truncated stock ID
        display_name = stock_id_to_ticker.get(stock_id, stock_id[:8] + "...")

        overview_data.append(
            {
                "Ticker": display_name,
                "Best Bid ($)": f"{float(best_bid['price']):.2f}" if best_bid else "-",
                "Best Ask ($)": f"{float(best_ask['price']):.2f}" if best_ask else "-",
                "Spread ($)": f"${spread:.2f}" if spread else "-",
                "Spread (%)": f"{spread_pct:.2f}%" if spread_pct else "-",
                "Bid Size": int(float(best_bid["quantity"])) if best_bid else 0,
                "Ask Size": int(float(best_ask["quantity"])) if best_ask else 0,
            }
        )

    if overview_data:
        df = pd.DataFrame(overview_data)
        st.dataframe(df, use_container_width=True)

        # Show last update time
        if all_order_books.get("timestamp"):
            st.caption(f"Last updated: {all_order_books['timestamp']}")
    else:
        st.info("No active order books found")


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
        "This is the dashboard for the Order Book Simulator, providing a visual "
        "representation of the various components of the system."
    )

    # Sidebar for controls
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
    st.sidebar.info(f"Last Updated: {datetime.now().strftime('%H:%M:%S')}")

    # TODO: Refactor this to use a more modular approach.
    # Main content area - conditional on view mode
    if view_mode == "Market Overview":
        create_market_overview()
    elif view_mode == "Single Stock" and ticker:
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
                st.caption(f"Last updated: {order_book_data['timestamp']}")
        else:
            st.info(f"No order book data available for {ticker}")


if __name__ == "__main__":
    main()
