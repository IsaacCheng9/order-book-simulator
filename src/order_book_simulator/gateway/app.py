import logging
from contextlib import asynccontextmanager
from datetime import datetime
from importlib.metadata import version

from fastapi import Depends, FastAPI, HTTPException

from order_book_simulator.common.models import OrderRequest, OrderResponse
from order_book_simulator.database.connection import get_db
from order_book_simulator.gateway.producer import OrderProducer
from order_book_simulator.gateway.validation import validate_order
from order_book_simulator.matching.consumer import OrderConsumer
from order_book_simulator.matching.engine import MatchingEngine

logger = logging.getLogger(__name__)


class AppState:
    def __init__(self):
        self.producer: OrderProducer | None = None
        self.consumer: OrderConsumer | None = None
        self.matching_engine: MatchingEngine | None = None


app_state = AppState()


async def publish_market_data(instrument_id, market_data):
    """Publishes market data updates (placeholder for now)."""
    # TODO: Implement market data publishing
    print(f"Market data update for {instrument_id}: {market_data}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Start-up logic
    try:
        app_state.matching_engine = MatchingEngine(publish_market_data)
        app_state.producer = OrderProducer()
        # Only start the producer, as we don't need the consumer in the
        # gateway.
        await app_state.producer.start()
        logger.info("Producer started successfully")
        yield
    except Exception as e:
        logger.error(f"Error during startup: {e}")
        raise
    finally:
        # Shut down the producer once we're done.
        if app_state.producer:
            await app_state.producer.stop()
            logger.info("Producer stopped")


package_version = version("order-book-simulator")
app = FastAPI(
    title="Order Book Simulator - Gateway Service",
    version=package_version,
    lifespan=lifespan,
)


@app.get("/health")
async def health_check():
    return {"status": "healthy", "timestamp": datetime.now(), "service": "gateway"}


@app.get("/health/kafka")
async def kafka_health() -> dict[str, str]:
    """Checks the Kafka producer's connectivity status."""
    status = {
        "kafka_producer": "unhealthy",
        "timestamp": datetime.now().isoformat(),
    }

    if not app_state.producer:
        status["error"] = "Kafka producer not initialised"
        raise HTTPException(status_code=503, detail=status)

    is_healthy = await app_state.producer.check_health()
    if is_healthy:
        status["kafka_producer"] = "healthy"
        return status

    status["error"] = "Failed to send health check message to Kafka"
    raise HTTPException(status_code=503, detail=status)


@app.post("/orders", response_model=OrderResponse)
async def create_order(order_request: OrderRequest, db=Depends(get_db)):
    await validate_order(order_request, db)

    async with db.transaction():
        query = """
        INSERT INTO order_ (
            user_id, instrument_id, type, side, price,
            quantity, time_in_force, client_order_id
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        RETURNING *
        """
        values = (
            order_request.user_id,
            order_request.instrument_id,
            order_request.type.value,
            order_request.side.value,
            order_request.price,
            order_request.quantity,
            order_request.time_in_force,
            order_request.client_order_id,
        )

        order_record = await db.fetchrow(query, *values)
        if app_state.producer is None:
            raise HTTPException(
                status_code=503, detail="Order processing service is unavailable."
            )
        await app_state.producer.send_order(order_record)

        return OrderResponse(**order_record)
