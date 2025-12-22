from order_book_simulator.gateway.producer import OrderProducer
from order_book_simulator.matching.consumer import OrderConsumer
from order_book_simulator.matching.matching_engine import MatchingEngine


class AppState:
    def __init__(self):
        self.producer: OrderProducer | None = None
        self.consumer: OrderConsumer | None = None
        self.matching_engine: MatchingEngine | None = None


app_state = AppState()
