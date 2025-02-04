import json

from aiokafka import AIOKafkaConsumer, ConsumerRecord

from order_book_simulator.matching.engine import MatchingEngine


class OrderConsumer:
    """Processes incoming orders from Kafka and routes them to the matching engine."""

    def __init__(self, matching_engine: MatchingEngine):
        """
        Creates a new order consumer.

        Args:
            matching_engine: The matching engine instance to process orders.
        """
        self.consumer = AIOKafkaConsumer(
            "orders", bootstrap_servers="kafka:9092", group_id="matching-engine"
        )
        self.matching_engine = matching_engine

    async def _process_message(self, message: ConsumerRecord) -> None:
        """
        Processes a single message from Kafka.

        Args:
            message: The message received from Kafka.
        """
        try:
            if message.value is None:
                return
            order_data = json.loads(message.value.decode("utf-8"))
            await self.matching_engine.process_order(order_data)
        except Exception as exception:
            print(f"Error processing message: {exception}")

    async def start(self) -> None:
        """Starts consuming order messages."""
        await self.consumer.start()
        try:
            async for message in self.consumer:
                await self._process_message(message)
        except Exception as exception:
            print(f"Error consuming messages: {exception}")
        finally:
            await self.consumer.stop()
