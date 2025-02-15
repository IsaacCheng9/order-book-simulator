import multiprocessing as mp
import time


class MarketSimulatorStats:
    """
    Tracks market simulator statistics with thread-safe counters using a
    rolling window.

    This class maintains a rolling window of order counts and timestamps to
    calculate
    rates over a specified time period. All counters are thread-safe using
    multiprocessing Value objects.

    Attributes:
        window_size: The size of the rolling window in seconds.
        order_count: Thread-safe counter for total orders.
        window_timestamps: List of timestamps in the current window.
        window_counts: List of order counts in the current window.
    """

    def __init__(self, window_size: float = 5.0):
        """
        Initialises the order statistics tracker.

        Args:
            window_size: Size of the rolling window in seconds.
        """
        self.order_count = mp.Value("i", 0)
        self.window_size = window_size
        self.window_timestamps: list[float] = []
        self.window_counts: list[int] = []

    def record_order(self) -> None:
        """Records an order in the counter."""
        with self.order_count.get_lock():
            self.order_count.value += 1

    def get_current_rate(self) -> float:
        """
        Gets the current order rate over the rolling window.

        Returns:
            The current order rate (orders per second)
        """
        current_time = time.time()
        with self.order_count.get_lock():
            self.window_timestamps.append(current_time)
            self.window_counts.append(self.order_count.value)

            while (
                len(self.window_timestamps) > 1
                and current_time - self.window_timestamps[0] > self.window_size
            ):
                self.window_timestamps.pop(0)
                self.window_counts.pop(0)

            if len(self.window_timestamps) > 1:
                window_elapsed = self.window_timestamps[-1] - self.window_timestamps[0]
                window_orders = self.window_counts[-1] - self.window_counts[0]
                return window_orders / window_elapsed if window_elapsed > 0 else 0
            return 0

    @property
    def total_orders(self) -> int:
        """Gets the total number of orders processed."""
        return self.order_count.value
