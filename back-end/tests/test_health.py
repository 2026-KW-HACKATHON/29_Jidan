import logging
from unittest.mock import MagicMock

import pymysql
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app import admin_password
from app.main import app

client = TestClient(app)


def test_health_is_public():
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "environment": "local", "database": "not_configured"}


def test_health_responses_carry_a_request_id_header():
    ok = client.get("/api/health")
    missing = client.get("/api/missing")
    assert ok.headers["X-Request-ID"].startswith("req_")
    assert missing.headers["X-Request-ID"] == missing.json()["requestId"]
    assert ok.headers["X-Request-ID"] != missing.headers["X-Request-ID"]


def test_unknown_api_is_not_a_successful_health_check():
    assert client.get("/api/missing").status_code == 404


def test_health_rejects_writes():
    assert client.post("/api/health").status_code == 405


@pytest.fixture(autouse=True)
def clear_environment(monkeypatch):
    for key in ("APP_ENV", "DB_HOST", "DB_NAME", "DB_USER", "DB_PASSWORD", "DB_PORT", admin_password.HASH_ENV):
        monkeypatch.delenv(key, raising=False)


ADMIN_PASSWORD = "health-check-admin-password"
VALID_ADMIN_HASH = admin_password.hash_password(ADMIN_PASSWORD, log2_n=admin_password.MIN_LOG2_N)


def configure_database(monkeypatch, environment="dev"):
    for key, value in {"APP_ENV": environment, "DB_HOST": "mysql", "DB_NAME": "jidan_dev",
                       "DB_USER": "jidan", "DB_PASSWORD": "test-only",
                       admin_password.HASH_ENV: VALID_ADMIN_HASH}.items():
        monkeypatch.setenv(key, value)


def healthy_database(monkeypatch):
    engine = MagicMock()
    engine.connect.return_value.__enter__.return_value.execute.return_value.scalar.return_value = 1
    monkeypatch.setattr("app.database.get_health_engine", lambda: engine)
    return engine


def test_database_is_checked_when_configured(monkeypatch):
    configure_database(monkeypatch)
    engine = MagicMock()
    connection = engine.connect.return_value.__enter__.return_value
    connection.execute.return_value.scalar.return_value = 1
    monkeypatch.setattr("app.database.get_health_engine", lambda: engine)
    response = client.get("/api/health")
    assert response.json() == {"status": "ok", "environment": "dev", "database": "ok"}
    assert str(connection.execute.call_args.args[0]) == "SELECT 1"


def test_unexpected_database_response_is_unavailable(monkeypatch):
    configure_database(monkeypatch)
    engine = MagicMock()
    engine.connect.return_value.__enter__.return_value.execute.return_value.scalar.return_value = 0
    monkeypatch.setattr("app.database.get_health_engine", lambda: engine)
    assert client.get("/api/health").status_code == 503


@pytest.mark.parametrize("environment", ["dev", "production", "invalid"])
def test_deployed_environment_requires_database(monkeypatch, environment):
    monkeypatch.setenv("APP_ENV", environment)
    monkeypatch.setenv(admin_password.HASH_ENV, VALID_ADMIN_HASH)  # isolate the missing database
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
    monkeypatch.setattr("app.database.get_health_engine", lambda: engine)
    response = client.get("/api/health")
    assert response.status_code == 503
    assert response.json() == {"detail": "Service unavailable"}
    assert "secret" not in response.text


def test_partial_local_database_configuration_is_rejected(monkeypatch):
    monkeypatch.setenv("DB_HOST", "mysql")
    assert client.get("/api/health").status_code == 503


# --- ADMIN_PASSWORD_HASH (deployed environments) -------------------------------------------------

@pytest.mark.parametrize("environment", ["dev", "production"])
@pytest.mark.parametrize("scheme", ["scrypt", "pbkdf2_sha256"])
def test_deployed_environment_accepts_a_valid_hash(monkeypatch, environment, scheme):
    configure_database(monkeypatch, environment)
    if scheme == "pbkdf2_sha256":
        monkeypatch.setenv(admin_password.HASH_ENV, admin_password.password_hash(ADMIN_PASSWORD))
    # Health validates configuration without running an expensive password derivation.
    monkeypatch.setattr(admin_password.hashlib, "pbkdf2_hmac", lambda *a, **k: pytest.fail("health derived a key"))
    healthy_database(monkeypatch)
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "environment": environment, "database": "ok"}


MALFORMED_ADMIN_HASHES = [
    "", "   ", "plain-password-do-not-print",
    # PBKDF2 below the approved minimum must fail closed.
    "pbkdf2_sha256$599999$c3Nzc3Nzc3Nzc3Nzc3Nzcw==$ZGRkZGRkZGRkZGRkZGRkZGRkZGRkZGRkZGRkZGRkZGQ=",
    VALID_ADMIN_HASH.replace("scrypt$", "bcrypt$", 1),
    VALID_ADMIN_HASH + "$extra",
    VALID_ADMIN_HASH.rsplit("$", 1)[0] + "$" + "A" * 42,  # 31-byte key
    "scrypt$13$8$1$" + VALID_ADMIN_HASH.split("$", 4)[4],  # N below the minimum
    "scrypt$18$8$1$" + VALID_ADMIN_HASH.split("$", 4)[4],  # needs more than MAX_MEMORY
    "scrypt$16$1$1$" + VALID_ADMIN_HASH.split("$", 4)[4],  # OpenSSL needs log2 N < 16 * r
    "scrypt$\u0661\u0664$8$1$" + VALID_ADMIN_HASH.split("$", 4)[4],  # non-ASCII digits
    VALID_ADMIN_HASH[:-2] + "+/",  # standard, not URL-safe, base64
    VALID_ADMIN_HASH[:-1] + "!",
]


@pytest.mark.parametrize("environment", ["dev", "production"])
@pytest.mark.parametrize("stored", MALFORMED_ADMIN_HASHES)
def test_deployed_environment_rejects_missing_or_malformed_hash(monkeypatch, caplog, environment, stored):
    configure_database(monkeypatch, environment)
    engine = healthy_database(monkeypatch)
    if stored == "":
        monkeypatch.delenv(admin_password.HASH_ENV)
    else:
        monkeypatch.setenv(admin_password.HASH_ENV, stored)
    with caplog.at_level(logging.DEBUG):
        response = client.get("/api/health")
    assert response.status_code == 503
    assert response.json() == {"detail": "Service unavailable"}
    for secret in (stored.strip(), ADMIN_PASSWORD):
        if secret:
            assert secret not in response.text and secret not in caplog.text
    # The configuration gate fails before the database is touched.
    engine.connect.assert_not_called()


def test_local_environment_does_not_require_admin_hash(monkeypatch):
    monkeypatch.setenv(admin_password.HASH_ENV, "plain-password-do-not-print")
    response = client.get("/api/health")
    assert response.status_code == 200
    assert "plain-password-do-not-print" not in response.text


def test_hash_with_surrounding_whitespace_matches_login(monkeypatch):
    # Login strips the value too, so health must not fail a hash that login accepts.
    configure_database(monkeypatch)
    healthy_database(monkeypatch)
    monkeypatch.setenv(admin_password.HASH_ENV, f" {VALID_ADMIN_HASH}\r")
    assert client.get("/api/health").status_code == 200
    assert admin_password.verify_password(ADMIN_PASSWORD)
