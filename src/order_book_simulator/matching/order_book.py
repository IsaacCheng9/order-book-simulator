from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID


@dataclass
class AggregatedLevel:
    """
    Represents an aggregated price level in the order book with total volume.
    """

    price: Decimal
    quantity: Decimal
    order_count: int


@dataclass
class OrderBookEntry:
    """
    Represents an individual order in the order book.
    """

    entry_id: UUID
    price: Decimal
    quantity: Decimal
    entry_time: int


@dataclass
class OrderBook:
    """
    Manages the limit order book for a single instrument.
    """

    def __init__(self, instrument_id: UUID):
        self.instrument_id = instrument_id
        self.bids: list[AggregatedLevel] = []
        self.asks: list[AggregatedLevel] = []
        self._bid_orders: list[OrderBookEntry] = []
        self._ask_orders: list[OrderBookEntry] = []
