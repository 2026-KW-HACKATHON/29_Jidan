from datetime import timedelta

import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session

from app import ratelimit, store_approvals
from app.admin_password import password_hash
from app.db import utcnow
from app.db.models import User
from tests.test_owner_stores import add_store
from tests.test_owner_stores import store_api as _store_api

store_api = _store_api
PATH = "/api/admin/store-approval-requests/search"
PASSWORD = " correct-password "
ORIGIN = {"Origin": "http://frontend.test"}


@pytest.fixture(scope="module")
def encoded_admin_password():
    return password_hash(PASSWORD)


@pytest.fixture
def admin_api(store_api, monkeypatch, encoded_admin_password):
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", encoded_admin_password)
    ratelimit.reset_all_limits()
    store_api.app.include_router(store_approvals.router)
    store_api.cookies.clear()  # body authentication does not require a member cookie
    yield store_api
    ratelimit.reset_all_limits()


def search(api, **body):
    return api.post(PATH, json={"password": PASSWORD, **body}, headers=ORIGIN)


def test_search_all_filtered_sorted_and_pages(admin_api, db_engine):
    now = utcnow()
    with Session(db_engine) as db:
        owner = db.get(User, admin_api.owner_id)
        _, a1 = add_store(db, owner)
        _, a2 = add_store(db, None, approval_status="APPROVED", approved_at=now)
        _, a3 = add_store(db, owner)
        a1.submitted_at = now - timedelta(seconds=1)
        a2.submitted_at = a3.submitted_at = now
        db.commit()
        expected = sorted([a2.id, a3.id], reverse=True) + [a1.id]
    all_rows = search(admin_api).json()
    assert [a["id"] for a in all_rows["items"]] == expected
    assert all_rows["totalItems"] == 3 and all_rows["page"] == 0 and all_rows["size"] == 20
    assert PASSWORD not in str(all_rows) and "password" not in str(all_rows)
    for status, count in (("PENDING", 2), ("APPROVED", 1)):
        result = search(admin_api, status=status, size=1).json()
        assert result["totalItems"] == result["totalPages"] == count
        assert len(result["items"]) == 1 and result["items"][0]["status"] == status
    assert search(admin_api, size=2, page=1).json()["items"][0]["id"] == expected[2]
    assert search(admin_api, page=10**100).json()["items"] == []


def test_empty_search(admin_api):
    result = search(admin_api).json()
    assert result["items"] == [] and result["totalItems"] == result["totalPages"] == 0


@pytest.mark.parametrize("body", [
    {}, {"password": None}, {"password": ""}, {"password": " "}, {"password": 123},
    {"password": "x" * 1025}, {"password": PASSWORD, "status": None},
    {"password": PASSWORD, "status": "pending"}, {"password": PASSWORD, "page": -1},
    {"password": PASSWORD, "page": True}, {"password": PASSWORD, "size": 101},
    {"password": PASSWORD, "size": "1"}, {"password": PASSWORD, "status": "REJECTED"},
    {"password": PASSWORD, "ownerId": "foreign"},
])
def test_search_input_boundaries(admin_api, body):
    response = admin_api.post(PATH, json=body, headers=ORIGIN)
    assert response.status_code == 422
    assert PASSWORD not in response.text


def test_authentication_before_database_and_rate_limit(admin_api, db_engine):
    reads = []
    def observe(connection, cursor, statement, parameters, context, many):
        if statement.lstrip().lower().startswith("select"):
            reads.append(statement)
    event.listen(db_engine, "before_cursor_execute", observe)
    try:
        for _ in range(5):
            response = admin_api.post(PATH, json={"password": "wrong"}, headers=ORIGIN)
            assert response.status_code == 401 and response.json()["code"] == "ADMIN_PASSWORD_INVALID"
        blocked = search(admin_api)
        assert blocked.status_code == 429 and int(blocked.headers["Retry-After"]) > 0
        assert reads == []
    finally:
        event.remove(db_engine, "before_cursor_execute", observe)


def test_success_resets_failed_attempts_and_origin_required(admin_api):
    for _ in range(4):
        assert admin_api.post(PATH, json={"password": "wrong"}, headers=ORIGIN).status_code == 401
    assert search(admin_api).status_code == 200
    for _ in range(4):
        assert admin_api.post(PATH, json={"password": "wrong"}, headers=ORIGIN).status_code == 401
    for headers in ({}, {"Origin": "https://foreign.test"}):
        response = admin_api.post(PATH, json={"password": PASSWORD}, headers=headers)
        assert response.status_code == 403 and response.json()["code"] == "CSRF_INVALID"


def test_password_and_validation_payload_absent_from_logs(admin_api, caplog, monkeypatch):
    secret = "never-log-this-password"
    assert admin_api.post(PATH, json={"password": secret}, headers=ORIGIN).status_code == 401
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", secret)
    assert search(admin_api).status_code == 500
    assert secret not in caplog.text and PASSWORD not in caplog.text
