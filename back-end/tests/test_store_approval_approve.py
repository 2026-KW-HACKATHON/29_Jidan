from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session

from app import auth, store_approvals, store_summary
from app.db.models import Store, StoreApprovalRequest, User
from tests.factories import NOW
from tests.test_owner_stores import add_store
from tests.test_store_approval_search import ORIGIN, PASSWORD
from tests.test_store_approval_search import admin_api as _admin_api
from tests.test_store_approval_search import encoded_admin_password as _encoded_admin_password
from tests.test_store_approval_search import store_api as _store_api

admin_api, encoded_admin_password, store_api = _admin_api, _encoded_admin_password, _store_api


def approve(api, request_id, **body):
    return api.post(f"/api/admin/store-approval-requests/{request_id}/approve",
                    json={"password": PASSWORD, **body}, headers=ORIGIN)


@pytest.fixture
def approval_target(admin_api, db_engine):
    with Session(db_engine) as db:
        store, approval = add_store(db, db.get(User, admin_api.owner_id))
        other, _ = add_store(db, db.get(User, admin_api.owner_id))
        issued = auth.create_session(admin_api.owner_id, db=db)
        db.commit()
        target = store.id, approval.id, other.id
    admin_api.cookies.set(auth.SESSION_COOKIE_NAME, issued.token)
    return target


def test_approve_and_retry_keep_first_time_and_session_permissions(admin_api, approval_target, db_engine, monkeypatch, caplog):
    store_id, request_id, other_id = approval_target
    admin_api.app.include_router(store_summary.router)
    assert admin_api.get(f"/api/stores/{store_id}/management-summary").status_code == 403
    assert admin_api.get("/api/auth/session").json()["nextAction"] == "OWNER_APPROVAL_PENDING"
    with caplog.at_level("INFO", logger="jidan.store_approval"):
        first = approve(admin_api, request_id)
    assert first.status_code == 200 and first.json()["status"] == "APPROVED"
    monkeypatch.setattr(store_approvals, "utcnow", lambda: NOW)
    repeated = approve(admin_api, request_id)
    assert repeated.json() == first.json()
    session = admin_api.get("/api/auth/session").json()
    assert session["nextAction"] == "OWNER_HOME"
    stores = {s["storeId"]: s for s in session["user"]["stores"]}
    assert len(stores[store_id]["permissions"]) == 5
    assert stores[other_id]["permissions"] == ["READ_STORE_STATUS"]
    assert admin_api.get(f"/api/stores/{store_id}/management-summary").status_code == 200
    with Session(db_engine) as db:
        store, request = db.get(Store, store_id), db.get(StoreApprovalRequest, request_id)
        assert store.approval_status == request.status == "APPROVED"
        assert store.approved_at == request.approved_at
    assert "request_id=" in caplog.text and request_id in caplog.text and PASSWORD not in caplog.text
    assert f"request_id={first.headers['X-Request-ID']} " in caplog.text


def test_authentication_precedes_existence(admin_api, db_engine):
    statements = []
    def observe(conn, cursor, statement, parameters, context, many):
        statements.append(statement)
    event.listen(db_engine, "before_cursor_execute", observe)
    try:
        assert approve(admin_api, uuid4(), password="wrong").status_code == 401
        assert not any("store_approval_requests" in s for s in statements)
        r = approve(admin_api, uuid4())
        assert r.status_code == 404 and r.json()["code"] == "STORE_APPROVAL_REQUEST_NOT_FOUND"
    finally:
        event.remove(db_engine, "before_cursor_execute", observe)


@pytest.mark.parametrize("state", ["worker", "suspended", "status_mismatch", "time_mismatch"])
def test_invalid_owner_or_inconsistent_state(admin_api, approval_target, db_engine, state):
    store_id, request_id, _ = approval_target
    with Session(db_engine) as db:
        user, store = db.get(User, admin_api.owner_id), db.get(Store, store_id)
        if state == "worker": user.role = "WORKER"
        if state == "suspended": user.status = "SUSPENDED"
        if state in {"status_mismatch", "time_mismatch"}:
            store.approval_status, store.approved_at = "APPROVED", NOW
        if state == "time_mismatch":
            from datetime import timedelta
            request = db.get(StoreApprovalRequest, request_id)
            request.status, request.approved_at = "APPROVED", NOW + timedelta(seconds=1)
        db.commit()
    r = approve(admin_api, request_id)
    assert r.status_code == 409 and r.json()["code"] == "STORE_APPROVAL_NOT_ALLOWED"


@pytest.mark.parametrize("field", ["status", "approvedAt", "permissions", "ownerId", "approverId"])
def test_approval_disallows_server_field_injection(admin_api, approval_target, field):
    assert approve(admin_api, approval_target[1], **{field: "injected"}).status_code == 422


@pytest.mark.parametrize("stage", ["flush", "commit"])
def test_approval_failure_rolls_back_both_rows(admin_api, approval_target, db_engine, monkeypatch, stage, caplog):
    store_id, request_id, _ = approval_target
    original = getattr(Session, stage)
    def fail(db, *args, **kwargs):
        if any(isinstance(row, (Store, StoreApprovalRequest)) for row in db.dirty):
            raise RuntimeError("secret-do-not-return")
        if stage == "commit" and db.info.get("test_approval_write"):
            raise RuntimeError("secret-do-not-return")
        return original(db, *args, **kwargs)
    def mark(db, context):
        if any(isinstance(row, StoreApprovalRequest) for row in db.dirty):
            db.info["test_approval_write"] = True
    event.listen(Session, "after_flush", mark)
    try:
        with monkeypatch.context() as m:
            m.setattr(Session, stage, fail)
            with caplog.at_level("INFO", logger="jidan.store_approval"):
                r = approve(admin_api, request_id)
            assert r.status_code == 500 and "secret" not in r.text
            assert r.headers["X-Request-ID"] == r.json()["requestId"]
            assert f"request_id={r.json()['requestId']} " in caplog.text
            assert "result=INTERNAL_ERROR" in caplog.text and "secret-do-not-return" not in caplog.text
    finally:
        event.remove(Session, "after_flush", mark)
    with Session(db_engine) as db:
        assert db.get(Store, store_id).approval_status == "PENDING"
        assert db.get(StoreApprovalRequest, request_id).status == "PENDING"
    assert approve(admin_api, request_id).status_code == 200


def test_concurrent_approval_applies_once(admin_api, approval_target, db_engine):
    if db_engine.dialect.name != "mysql":
        pytest.skip("MySQL row locking required")
    barrier = Barrier(5)
    writes = []
    def observe(conn, cursor, statement, parameters, context, many):
        if statement.lower().startswith("update stores ") or statement.lower().startswith("update store_approval_requests "):
            writes.append(statement)
    event.listen(db_engine, "before_cursor_execute", observe)
    def run(_):
        barrier.wait(timeout=10)
        return approve(admin_api, approval_target[1])
    try:
        with ThreadPoolExecutor(max_workers=5) as pool:
            responses = list(pool.map(run, range(5)))
    finally:
        event.remove(db_engine, "before_cursor_execute", observe)
    assert [r.status_code for r in responses] == [200] * 5
    assert all(r.json() == responses[0].json() for r in responses)
    assert len(writes) == 2


def test_approve_requires_origin_and_valid_uuid(admin_api, approval_target):
    path = f"/api/admin/store-approval-requests/{approval_target[1]}/approve"
    for headers in ({}, {"Origin": "https://foreign.test"}):
        assert admin_api.post(path, json={"password": PASSWORD}, headers=headers).status_code == 403
    assert approve(admin_api, "invalid").status_code == 422


@pytest.mark.parametrize("scenario,status", [("missing", 404), ("role", 409), ("password", 401)])
def test_failed_approval_audit_id_matches_client_response(admin_api, approval_target, db_engine, caplog,
                                                        scenario, status):
    request_id = approval_target[1]
    password = PASSWORD
    if scenario == "missing": request_id = str(uuid4())
    if scenario == "password": password = "wrong-password"
    if scenario == "role":
        with Session(db_engine) as db:
            db.get(User, admin_api.owner_id).role = "WORKER"
            db.commit()
    path = f"/api/admin/store-approval-requests/{request_id}/approve"
    with caplog.at_level("INFO", logger="jidan.store_approval"):
        response = admin_api.post(path, json={"password": password},
                                  headers={**ORIGIN, "X-Request-ID": "untrusted-client-id"})
    assert response.status_code == status
    correlation = response.json()["requestId"]
    assert response.headers["X-Request-ID"] == correlation and correlation != "untrusted-client-id"
    records = [r.getMessage() for r in caplog.records if r.name == "jidan.store_approval"]
    assert len(records) == 1
    assert f"request_id={correlation} " in records[0]
    assert f"approval_request_id={request_id} " in records[0]
    assert f"result={response.json()['code']}" in records[0]
    assert password not in records[0] and "untrusted-client-id" not in records[0]
