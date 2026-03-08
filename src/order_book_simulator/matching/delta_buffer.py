import time
from uuid import UUID
from decimal import Decimal
from collections import deque
from order_book_simulator.common.models import DeltaType, OrderSide, Delta


class DeltaBuffer:
    """
    Bounded buffer for incremental order book deltas with monotonic sequence
    numbering.

    Stores the most recent deltas up to max_size. When a client requests deltas
    older than the buffer window, returns None to signal that a full snapshot
    is needed.
    """

    def __init__(self, max_size: int = 2000):
        self._sequence_number: int = 0
        self._buffer: deque[Delta] = deque(maxlen=max_size)

    def add(
        self,
        delta_type: DeltaType,
        ticker: str,
        side: OrderSide | None,
        price: Decimal,
        quantity: Decimal,
        order_count: int | None = None,
        trade_id: UUID | None = None,
    ) -> Delta:
        """
        Create a delta with the next sequence number and append it to the
        buffer.

        Args:
            delta_type: The type of delta event.
            ticker: The stock ticker symbol.
            side: The order side, or None for trade deltas.
            price: The price level affected.
            quantity: The new total quantity at this level.
            order_count: Number of orders at this level.
            trade_id: The trade ID, only for TRADE deltas.

        Returns:
            The created Delta with its assigned sequence number.
        """
        self._sequence_number += 1
        new_delta = Delta(
            sequence_number=self._sequence_number,
            timestamp=time.time(),
            delta_type=delta_type,
            ticker=ticker,
            side=side,
            price=price,
            quantity=quantity,
            order_count=order_count,
            trade_id=trade_id,
        )
        self._buffer.append(new_delta)
        return new_delta

    def get_delta_since(self, sequence_number: int) -> list[Delta] | None:
        """
        Return all deltas after the given sequence number.

        Args:
            sequence_number: The last sequence number the client has seen.

        Returns:
            A list of deltas newer than the given sequence number, an empty
            list if the client is up to date, or None if the requested sequence
            has been evicted and the client needs a full snapshot.
        """
        if sequence_number >= self._sequence_number:
            return []
        # Client is too far behind - requested sequence number is older than
        # the oldest delta in the buffer.
        elif not self._buffer or sequence_number < self._buffer[0].sequence_number - 1:
            return None
        return [
            delta for delta in self._buffer if delta.sequence_number > sequence_number
        ]

    @property
    def current_sequence(self) -> int:
        return self._sequence_number
