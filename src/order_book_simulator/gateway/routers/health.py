from datetime import datetime

from fastapi import APIRouter, HTTPException

from order_book_simulator.gateway.app_state import app_state

router = APIRouter(tags=["health"])


@router.get("")
async def health_check():
    return {"status": "healthy", "timestamp": datetime.now(), "service": "gateway"}


@router.get("/kafka")
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
