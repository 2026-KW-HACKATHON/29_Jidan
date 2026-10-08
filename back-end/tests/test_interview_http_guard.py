"""Reject unsafe HTTP fixture database targets before creating a connection."""
import pytest

from e2e.conftest import require_local_test_database


@pytest.mark.parametrize("host", ["e2e-mysql", "127.0.0.1", "localhost"])
def test_http_database_guard_accepts_only_isolated_local_targets(monkeypatch, host):
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("DB_NAME", "jidan_e2e_test")
    monkeypatch.setenv("DB_HOST", host)
    require_local_test_database()


@pytest.mark.parametrize(("name", "value"), [
    ("DB_HOST", "production-db"), ("DB_HOST", ""),
    ("DB_NAME", "jidan_dev"), ("DB_NAME", "other_test"), ("APP_ENV", "production"),
])
def test_http_database_guard_rejects_wrong_host_database_or_environment(monkeypatch, name, value):
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("DB_NAME", "jidan_e2e_test")
    monkeypatch.setenv("DB_HOST", "e2e-mysql")
    monkeypatch.setenv(name, value)
    with pytest.raises(pytest.fail.Exception):
        require_local_test_database()
