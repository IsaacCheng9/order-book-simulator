def test_health_check(test_client):
    """Verifies the health check endpoint returns the expected response."""
    response = test_client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"
    assert "timestamp" in response.json()
