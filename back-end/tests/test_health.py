from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_is_public():
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_unknown_api_is_not_a_successful_health_check():
    assert client.get("/api/missing").status_code == 404


def test_health_rejects_writes():
    assert client.post("/api/health").status_code == 405
