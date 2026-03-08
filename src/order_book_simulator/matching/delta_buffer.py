import time
from uuid import UUID
from decimal import Decimal
from collections import deque
from order_book_simulator.common.models import DeltaType, OrderSide, Delta


class DeltaBuffer:
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
        order_count: int,
        trade_id: UUID | None = None,
    ) -> Delta:
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

    def get_delta_since(self, sequence: int) -> list[Delta] | None:
        raise NotImplementedError

    @property
    def current_sequence(self) -> int:
        return self._sequence_number
