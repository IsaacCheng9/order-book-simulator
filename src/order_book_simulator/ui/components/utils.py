from datetime import datetime


def format_timestamp(iso_timestamp: str) -> str:
    """
    Formats an ISO timestamp to a more readable format.

    Args:
        iso_timestamp: ISO format timestamp string

    Returns:
        The timestamp string in a human-readable format
    """
    try:
        # Parse the ISO timestamp
        dt = datetime.fromisoformat(iso_timestamp.replace("Z", "+00:00"))
        # Format as: "30 Dec 2024 at 14:25:23 UTC"
        return dt.strftime("%d %b %Y at %H:%M:%S UTC")
    except (ValueError, AttributeError):
        # Fallback to original if parsing fails
        return iso_timestamp
