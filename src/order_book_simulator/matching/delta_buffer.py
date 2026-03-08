from uuid import UUID
from decimal import Decimal
from collections import deque
from order_book_simulator.common.models import DeltaType, OrderSide, Delta


class DeltaBuffer:
    def __init__(self, max_size: int = 2000):
        self._sequence = 0
        self._buffer = deque(maxlen=max_size)

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
        raise NotImplementedError

    def get_delta_since(self, sequence: int) -> list[Delta] | None:
        raise NotImplementedError

    @property
    def current_sequence(self) -> int:
        return self._sequence
