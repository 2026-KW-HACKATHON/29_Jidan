from unittest.mock import MagicMock

import pymysql
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.main import app

VALID_HASH = "pbkdf2_sha256$600000$c3Nzc3Nzc3Nzc3Nzc3Nzcw==$ZGRkZGRkZGRkZGRkZGRkZGRkZGRkZGRkZGRkZGRkZGQ="

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
    for key in ("APP_ENV", "DB_HOST", "DB_NAME", "DB_USER", "DB_PASSWORD", "DB_PORT", "ADMIN_PASSWORD_HASH"):
        monkeypatch.delenv(key, raising=False)


def configure_database(monkeypatch):
    for key, value in {"APP_ENV": "dev", "DB_HOST": "mysql", "DB_NAME": "jidan_dev",
                       "DB_USER": "jidan", "DB_PASSWORD": "test-only",
                       "ADMIN_PASSWORD_HASH": VALID_HASH}.items():
        monkeypatch.setenv(key, value)


def test_database_is_checked_when_configured(monkeypatch):
    configure_database(monkeypatch)
    engine = MagicMock()
    connection = engine.connect.return_value.__enter__.return_value
    connection.execute.return_value.scalar.return_value = 1
    monkeypatch.setattr("app.database.get_engine", lambda: engine)
    response = client.get("/api/health")
    assert response.json() == {"status": "ok", "environment": "dev", "database": "ok"}
    assert str(connection.execute.call_args.args[0]) == "SELECT 1"


def test_unexpected_database_response_is_unavailable(monkeypatch):
    configure_database(monkeypatch)
    engine = MagicMock()
    engine.connect.return_value.__enter__.return_value.execute.return_value.scalar.return_value = 0
    monkeypatch.setattr("app.database.get_engine", lambda: engine)
    assert client.get("/api/health").status_code == 503


@pytest.mark.parametrize("environment", ["dev", "production", "invalid"])
def test_deployed_environment_requires_database(monkeypatch, environment):
    monkeypatch.setenv("APP_ENV", environment)
    assert client.get("/api/health").status_code == 503


@pytest.mark.parametrize("port", ["zero", "0", "65536"])
def test_invalid_database_port(monkeypatch, port):
    configure_database(monkeypatch)
    monkeypatch.setenv("DB_PORT", port)
    assert client.get("/api/health").status_code == 503


@pytest.mark.parametrize("error", [
    pymysql.OperationalError("secret-password-and-host"),
    OperationalError("SELECT 1", {}, Exception("secret-password-and-host")),
])
def test_database_failure_does_not_expose_credentials(monkeypatch, error):
    configure_database(monkeypatch)
    engine = MagicMock()
    engine.connect.side_effect = error
    monkeypatch.setattr("app.database.get_engine", lambda: engine)
    response = client.get("/api/health")
    assert response.status_code == 503
    assert response.json() == {"detail": "Service unavailable"}
    assert "secret" not in response.text


def test_partial_local_database_configuration_is_rejected(monkeypatch):
    monkeypatch.setenv("DB_HOST", "mysql")
    assert client.get("/api/health").status_code == 503


@pytest.mark.parametrize("environment", ["dev", "production"])
@pytest.mark.parametrize("configured", [None, "", "do-not-print-secret", "pbkdf2_sha256$1$a$b"])
def test_deployed_health_requires_valid_admin_hash(monkeypatch, environment, configured):
    configure_database(monkeypatch)
    monkeypatch.setenv("APP_ENV", environment)
    if configured is None:
        monkeypatch.delenv("ADMIN_PASSWORD_HASH")
    else:
        monkeypatch.setenv("ADMIN_PASSWORD_HASH", configured)
    engine = MagicMock()
    monkeypatch.setattr("app.database.get_engine", lambda: engine)
    response = client.get("/api/health")
    assert response.status_code == 503
    assert response.json() == {"detail": "Service unavailable"}
    assert "secret" not in response.text
    engine.connect.assert_not_called()


@pytest.mark.parametrize("environment", ["dev", "production"])
def test_deployed_health_checks_hash_without_deriving_password(monkeypatch, environment):
    configure_database(monkeypatch)
    monkeypatch.setenv("APP_ENV", environment)
    engine = MagicMock()
    engine.connect.return_value.__enter__.return_value.execute.return_value.scalar.return_value = 1
    monkeypatch.setattr("app.database.get_engine", lambda: engine)
    monkeypatch.setattr("hashlib.pbkdf2_hmac", lambda *args: pytest.fail("Health must not derive passwords"))
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "environment": environment, "database": "ok"}
