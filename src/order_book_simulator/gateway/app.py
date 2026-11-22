import asyncio
import logging
import os
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from importlib.metadata import version
from typing import Any

from fastapi import FastAPI
from redis.asyncio import Redis

from order_book_simulator.common.cache import order_book_cache
from order_book_simulator.database.get_stocks import load_all_us_stocks
from order_book_simulator.gateway.app_state import app_state
from order_book_simulator.gateway.producer import OrderProducer
from order_book_simulator.gateway.routers import (
    health_router,
    market_data_router,
    order_book_router,
    ws_router,
)
from order_book_simulator.gateway.ws_manager import (
    redis_pubsub_delta_subscriber,
    ws_manager,
)
from order_book_simulator.market_data.analytics import MarketDataAnalytics

logger = logging.getLogger(__name__)


async def _run_cleanup(label: str, action: Callable[[], Awaitable[Any]]) -> None:
    """Run one shutdown step without masking earlier errors or later teardowns."""
    try:
        await action()
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Error during gateway shutdown ({label}): {exc}")


async def _cancel_subscriber_task(task: asyncio.Task[Any]) -> None:
    """Cancel and await the Redis Pub/Sub subscriber task."""
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Start-up logic.
    try:
        # Add the stocks to the database.
        await load_all_us_stocks()
        redis_url = os.getenv("REDIS_URL", "redis://redis:6379/0")
        app_state.analytics_redis = Redis.from_url(redis_url, decode_responses=True)
        app_state.analytics = MarketDataAnalytics(app_state.analytics_redis)
        producer = OrderProducer()
        # Only start the producer, as we don't need the consumer in the
        # gateway.
        await producer.start()
        app_state.producer = producer
        app_state.redis_subscriber_task = asyncio.create_task(
            redis_pubsub_delta_subscriber(redis_url=redis_url)
        )
        logger.info("Producer started successfully")
        yield
    except Exception as e:
        logger.error(f"Error during startup: {e}")
        raise
    finally:
        await _run_cleanup("closing WebSocket connections", ws_manager.close_all)

        # Cancel the Redis Pub/Sub subscriber task.
        subscriber_task = app_state.redis_subscriber_task
        app_state.redis_subscriber_task = None
        if subscriber_task is not None:
            await _run_cleanup(
                "cancelling Redis Pub/Sub subscriber task",
                lambda: _cancel_subscriber_task(subscriber_task),
            )

        # Shut down the producer once we're done.
        producer = app_state.producer
        app_state.producer = None
        if producer is not None:
            await _run_cleanup("stopping Kafka producer", producer.stop)
            logger.info("Producer stopped")

        analytics_redis = app_state.analytics_redis
        app_state.analytics_redis = None
        app_state.analytics = None
        if analytics_redis is not None:
            await _run_cleanup("closing analytics Redis client", analytics_redis.aclose)

        await _run_cleanup(
            "closing order book cache Redis client", order_book_cache.close
        )


package_version = version("order-book-simulator")
app = FastAPI(
    title="Order Book Simulator - Gateway Service",
    version=package_version,
    lifespan=lifespan,
)

# We have separate routers for each of the resources.
app.include_router(health_router.health_router, prefix="/v1/health", tags=["health"])
app.include_router(
    order_book_router.order_book_router, prefix="/v1/order-book", tags=["order-book"]
)
app.include_router(
    market_data_router.market_data_router,
    prefix="/v1/market-data",
    tags=["market-data"],
)
app.include_router(ws_router.ws_router, prefix="/v1/ws", tags=["websocket", "ws"])
