from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.admin_password import AdminPasswordInput, password_hash, verify_password
from app.errors import ApiError


@pytest.fixture(scope="module")
def admin_hash():
    return password_hash(" correct-password ")


def attempt_result():
    results = []
    return SimpleNamespace(failed=lambda: results.append("failed"),
                           succeeded=lambda: results.append("succeeded")), results


def test_password_preserves_whitespace_and_secret_repr(monkeypatch, admin_hash):
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", admin_hash)
    body = AdminPasswordInput(password=" correct-password ")
    assert body.password.get_secret_value() == " correct-password "
    assert "correct-password" not in repr(body) and "correct-password" not in body.model_dump_json()
    attempt, results = attempt_result()
    verify_password(body.password, attempt)
    assert results == ["succeeded"]
    for value in ("correct-password", " wrong "):
        attempt, results = attempt_result()
        with pytest.raises(ApiError) as exc:
            verify_password(AdminPasswordInput(password=value).password, attempt)
        assert exc.value.status_code == 401 and results == ["failed"]


@pytest.mark.parametrize("value", [None, 1, [], "", " \n\t", "x" * 1025])
def test_invalid_password_input(value):
    with pytest.raises(ValidationError):
        AdminPasswordInput(password=value)


@pytest.mark.parametrize("configured", [None, "", "plaintext-secret", "pbkdf2_sha256$1$a$b",
                                        "pbkdf2_sha256$999999999$a$b"])
def test_bad_configuration_fails_closed(monkeypatch, configured):
    if configured is None: monkeypatch.delenv("ADMIN_PASSWORD_HASH", raising=False)
    else: monkeypatch.setenv("ADMIN_PASSWORD_HASH", configured)
    attempt, results = attempt_result()
    with pytest.raises(ApiError) as exc:
        verify_password(AdminPasswordInput(password="secret-value").password, attempt)
    assert exc.value.status_code == 500 and results == ["failed"]
    assert "secret" not in str(exc.value)


def test_salts_are_unique():
    assert password_hash("same") != password_hash("same")
