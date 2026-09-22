from unittest.mock import MagicMock

import pymysql
import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_is_public():
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "environment": "local", "database": "not_configured"}


def test_unknown_api_is_not_a_successful_health_check():
    assert client.get("/api/missing").status_code == 404


def test_health_rejects_writes():
    assert client.post("/api/health").status_code == 405


@pytest.fixture(autouse=True)
def clear_environment(monkeypatch):
    for key in ("APP_ENV", "DB_HOST", "DB_NAME", "DB_USER", "DB_PASSWORD", "DB_PORT"):
        monkeypatch.delenv(key, raising=False)


def configure_database(monkeypatch):
    for key, value in {"APP_ENV": "dev", "DB_HOST": "mysql", "DB_NAME": "jidan_dev",
                       "DB_USER": "jidan", "DB_PASSWORD": "test-only"}.items():
        monkeypatch.setenv(key, value)


def test_database_is_checked_when_configured(monkeypatch):
    configure_database(monkeypatch)
    connect = MagicMock()
    cursor = connect.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
    cursor.fetchone.return_value = (1,)
    monkeypatch.setattr("app.database.pymysql.connect", connect)
    response = client.get("/api/health")
    assert response.json() == {"status": "ok", "environment": "dev", "database": "ok"}
    cursor.execute.assert_called_once_with("SELECT 1")
    assert connect.call_args.kwargs["database"] == "jidan_dev"


@pytest.mark.parametrize("environment", ["dev", "production", "invalid"])
def test_deployed_environment_requires_database(monkeypatch, environment):
    monkeypatch.setenv("APP_ENV", environment)
    assert client.get("/api/health").status_code == 503


@pytest.mark.parametrize("port", ["zero", "0", "65536"])
def test_invalid_database_port(monkeypatch, port):
    configure_database(monkeypatch)
    monkeypatch.setenv("DB_PORT", port)
    assert client.get("/api/health").status_code == 503


def test_database_failure_does_not_expose_credentials(monkeypatch):
    configure_database(monkeypatch)
    monkeypatch.setattr("app.database.pymysql.connect", MagicMock(
        side_effect=pymysql.OperationalError("secret-password-and-host")))
    response = client.get("/api/health")
    assert response.status_code == 503
    assert response.json() == {"detail": "Service unavailable"}


def test_partial_local_database_configuration_is_rejected(monkeypatch):
    monkeypatch.setenv("DB_HOST", "mysql")
    assert client.get("/api/health").status_code == 503
