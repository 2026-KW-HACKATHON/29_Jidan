"""Exercise escaped surrogate JSON through both administrator HTTP endpoints."""
import json
from uuid import uuid4

import pytest
from sqlalchemy import event

from tests.test_store_approval_search import ORIGIN, PASSWORD
from tests.test_store_approval_search import admin_api as _admin_api
from tests.test_store_approval_search import encoded_admin_password as _encoded_admin_password
from tests.test_store_approval_search import store_api as _store_api

admin_api, encoded_admin_password, store_api = _admin_api, _encoded_admin_password, _store_api


def post_password(api, path, password):
    # httpx's normal JSON serializer encodes UTF-8 locally. ASCII escapes let
    # malformed Unicode reach the server, like a client supplying raw JSON.
    return api.post(path, content=json.dumps({"password": password}, ensure_ascii=True).encode("ascii"),
                    headers={**ORIGIN, "Content-Type": "application/json"})


@pytest.fixture(params=["search", "approve"])
def admin_path(request):
    root = "/api/admin/store-approval-requests"
    return root + ("/search" if request.param == "search" else f"/{uuid4()}/approve")


@pytest.mark.parametrize("value", [chr(0xD800), chr(0xDFFF), "before" + chr(0xD800) + "after"])
def test_surrogate_json_fails_auth_before_database(admin_api, db_engine, admin_path, monkeypatch, value, caplog):
    caplog.set_level("INFO", logger="jidan.store_approval")
    reads = []
    def observe(connection, cursor, statement, parameters, context, many):
        if statement.lstrip().lower().startswith("select"):
            reads.append(statement)
    event.listen(db_engine, "before_cursor_execute", observe)
    monkeypatch.setattr("hashlib.pbkdf2_hmac", lambda *args: pytest.fail("Invalid UTF-8 must not derive passwords"))
    try:
        response = post_password(admin_api, admin_path, value)
    finally:
        event.remove(db_engine, "before_cursor_execute", observe)
    assert response.status_code == 401
    assert response.json()["code"] == "ADMIN_PASSWORD_INVALID"
    assert response.json()["requestId"] == response.headers["X-Request-ID"]
    assert reads == []
    assert "UnicodeEncodeError" not in caplog.text
    assert "before" not in caplog.text
    assert "\\ud800" not in response.text and "\\udfff" not in response.text


def test_surrogate_failures_count_toward_rate_limit(admin_api, admin_path):
    for _ in range(5):
        assert post_password(admin_api, admin_path, "\ud800").status_code == 401
    limited = post_password(admin_api, admin_path, PASSWORD)
    assert limited.status_code == 429 and limited.json()["code"] == "RATE_LIMITED"
    assert int(limited.headers["Retry-After"]) > 0


def test_success_clears_completed_surrogate_failures(admin_api, admin_path):
    for _ in range(2):
        for _ in range(4):
            assert post_password(admin_api, admin_path, "\udfff").status_code == 401
        success = post_password(admin_api, admin_path, PASSWORD)
        # Approval uses a missing ID: authentication succeeds before its 404.
        expected = 200 if admin_path.endswith("/search") else 404
        assert success.status_code == expected


def test_valid_json_surrogate_pair_authenticates(admin_api, monkeypatch):
    from app.admin_password import password_hash

    password = "비밀번호🙂"
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", password_hash(password))
    response = post_password(admin_api, "/api/admin/store-approval-requests/search", password)
    assert response.status_code == 200
