from datetime import datetime

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
    _ = st.sidebar.selectbox(
        "Select Stock",
        ["AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA"],
    )

    # Last update time
    st.sidebar.info(f"Last Updated: {datetime.now().strftime('%H:%M:%S')}")


if __name__ == "__main__":
    main()
