from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from order_book_simulator.gateway.app_state import app_state

health_router = APIRouter()


@health_router.get("")
async def health_check() -> JSONResponse:
    """Report gateway health including Kafka producer and Redis fan-out."""
    producer_ok = app_state.producer is not None
    subscriber_task = app_state.redis_subscriber_task
    redis_fanout_ok = (
        app_state.redis_pubsub_healthy
        and subscriber_task is not None
        and not subscriber_task.done()
    )
    payload: dict[str, Any] = {
        "status": "healthy" if (producer_ok and redis_fanout_ok) else "degraded",
        "timestamp": datetime.now(UTC).isoformat(),
        "service": "gateway",
        "components": {
            "kafka_producer": "healthy" if producer_ok else "unhealthy",
            "redis_fanout": "healthy" if redis_fanout_ok else "unhealthy",
        },
    }
    if app_state.redis_pubsub_error and not redis_fanout_ok:
        payload["components"]["redis_fanout_error"] = app_state.redis_pubsub_error
    status_code = 200 if (producer_ok and redis_fanout_ok) else 503
    return JSONResponse(status_code=status_code, content=payload)


@health_router.get("/kafka")
async def kafka_health() -> dict[str, str]:
    """Checks the Kafka producer's connectivity status."""
    status = {
        "kafka_producer": "unhealthy",
        "timestamp": datetime.now(UTC).isoformat(),
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
