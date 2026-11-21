from unittest.mock import MagicMock

from order_book_simulator.gateway.app_state import app_state


def test_health_check_healthy(test_client, monkeypatch):
    """Returns 200 healthy when producer and Redis fan-out are active."""
    fake_task = MagicMock()
    fake_task.done.return_value = False
    monkeypatch.setattr(app_state, "producer", MagicMock())
    monkeypatch.setattr(app_state, "redis_subscriber_task", fake_task)
    monkeypatch.setattr(app_state, "redis_pubsub_healthy", True)
    monkeypatch.setattr(app_state, "redis_pubsub_error", None)

    response = test_client.get("/v1/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "healthy"
    assert body["components"]["kafka_producer"] == "healthy"
    assert body["components"]["redis_fanout"] == "healthy"
    assert "timestamp" in body


def test_health_check_degraded_when_redis_fanout_down(test_client, monkeypatch):
    """Returns 503 degraded when the Redis Pub/Sub fan-out task is unhealthy."""
    fake_task = MagicMock()
    fake_task.done.return_value = True
    monkeypatch.setattr(app_state, "producer", MagicMock())
    monkeypatch.setattr(app_state, "redis_subscriber_task", fake_task)
    monkeypatch.setattr(app_state, "redis_pubsub_healthy", False)
    monkeypatch.setattr(app_state, "redis_pubsub_error", "connection lost")

    response = test_client.get("/v1/health")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert body["components"]["redis_fanout"] == "unhealthy"
    assert body["components"]["redis_fanout_error"] == "connection lost"
