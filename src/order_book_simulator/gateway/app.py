import logging
from contextlib import asynccontextmanager
from importlib.metadata import version

from fastapi import FastAPI

from order_book_simulator.database.get_stocks import get_all_stocks
from order_book_simulator.gateway.app_state import app_state
from order_book_simulator.gateway.producer import OrderProducer
from order_book_simulator.gateway.routers import health, market_data, orders

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Start-up logic
    try:
        # Add the stocks to the database.
        await get_all_stocks()
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

# We have separate routers for each of the resources.
app.include_router(health.router)
app.include_router(orders.router)
app.include_router(market_data.router)
