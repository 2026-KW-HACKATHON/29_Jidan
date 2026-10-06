from unittest.mock import patch

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import owner_stores
from app.db.models import IdempotencyRecord, Store, StoreApprovalRequest
from app.errors import ApiError, ErrorCode
from tests.test_owner_registration import OWNER
from tests.test_owner_stores import add_store
from tests.test_owner_stores import store_api as _store_api
from tests.test_worker_registration import headers

store_api = _store_api
BODY = OWNER["store"]


@pytest.fixture(autouse=True)
def address_provider(monkeypatch):
    monkeypatch.setattr(owner_stores, "verify_store_address", lambda body: body.address)


def test_creation_atomic_pending_and_replay(store_api, db_engine):
    h = headers(store_api)
    first = store_api.post("/api/stores", json=BODY, headers=h)
    assert first.status_code == 201
    assert first.json()["permissions"] == ["READ_STORE_STATUS"]
    assert first.json()["approvalStatus"] == "PENDING" and first.json()["approvedAt"] is None
    with patch.object(owner_stores, "verify_store_address", side_effect=AssertionError("provider rerun")):
        retry = store_api.post("/api/stores", json=BODY, headers=h)
    assert retry.json() == first.json() and retry.headers["Idempotent-Replayed"] == "true"
    assert "set-cookie" not in retry.headers
    conflict = store_api.post("/api/stores", json={**BODY, "name": "changed"}, headers=h)
    assert conflict.status_code == 409 and conflict.json()["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert store_api.get("/api/owners/me/stores").json()["items"][0] == first.json()
    assert store_api.get("/api/auth/session").json()["user"]["stores"][0]["storeId"] == first.json()["id"]
    with Session(db_engine) as db:
        for table in (Store, StoreApprovalRequest, IdempotencyRecord):
            assert db.scalar(select(func.count()).select_from(table)) == 1


def test_foreign_duplicate_does_not_grant_ownership(store_api, db_engine):
    from tests.factories import make_user
    with Session(db_engine) as db:
        add_store(db, make_user(db, "OWNER"), business_registration_number=BODY["businessRegistrationNumber"])
        db.commit()
    r = store_api.post("/api/stores", json=BODY, headers=headers(store_api))
    assert r.status_code == 409 and r.json()["code"] == "STORE_ALREADY_REGISTERED"
    assert store_api.get("/api/owners/me/stores").json()["items"] == []


@pytest.mark.parametrize("change", [
    {"ownerId": "injected"}, {"approvalStatus": "APPROVED"}, {"name": " "},
    {"name": "x" * 101}, {"businessRegistrationNumber": "１２３４５６７８９０"},
    {"businessRegistrationNumber": "123-4567890"}, {"postalCode": "1234"},
    {"phoneNumber": "123456789"}, {"detailAddress": None}, {"industry": "cafe"},
])
def test_invalid_create_input(store_api, change):
    assert store_api.post("/api/stores", json={**BODY, **change}, headers=headers(store_api)).status_code == 422


@pytest.mark.parametrize("bad", ["origin", "csrf", "key_missing", "key_invalid", "no_session"])
def test_creation_security(store_api, bad):
    h = headers(store_api)
    status = 403
    if bad == "origin": h["Origin"] = "https://foreign.test"
    if bad == "csrf": h["X-CSRF-Token"] = "invalid"
    if bad == "key_missing": h.pop("Idempotency-Key"); status = 422
    if bad == "key_invalid": h["Idempotency-Key"] = "invalid"; status = 422
    if bad == "no_session": store_api.cookies.clear(); status = 401
    assert store_api.post("/api/stores", json=BODY, headers=h).status_code == status


@pytest.mark.parametrize("stage", ["address", "flush", "commit"])
def test_create_failure_rolls_back_and_key_can_retry(store_api, db_engine, monkeypatch, stage):
    h = headers(store_api)
    original_flush, original_commit = Session.flush, Session.commit
    def fail_flush(db, *args, **kwargs):
        if any(isinstance(row, StoreApprovalRequest) for row in db.new):
            raise RuntimeError("secret-value")
        return original_flush(db, *args, **kwargs)
    def fail_commit(db):
        if db.scalar(select(Store.id)):
            raise RuntimeError("secret-value")
        return original_commit(db)
    with monkeypatch.context() as m:
        if stage == "address":
            def fail(body): raise ApiError(422, ErrorCode.STORE_OUTSIDE_SERVICE_AREA)
            m.setattr(owner_stores, "verify_store_address", fail)
        if stage == "flush": m.setattr(Session, "flush", fail_flush)
        if stage == "commit": m.setattr(Session, "commit", fail_commit)
        r = store_api.post("/api/stores", json=BODY, headers=h)
        assert r.status_code == (422 if stage == "address" else 500)
        assert "secret-value" not in r.text
    with Session(db_engine) as db:
        for table in (Store, StoreApprovalRequest, IdempotencyRecord):
            assert db.scalar(select(func.count()).select_from(table)) == 0
    assert store_api.post("/api/stores", json=BODY, headers=h).status_code == 201


def test_replay_rechecks_ownership(store_api, db_engine):
    from tests.factories import make_user
    h = headers(store_api)
    assert store_api.post("/api/stores", json=BODY, headers=h).status_code == 201
    with Session(db_engine) as db:
        db.scalar(select(Store)).owner_id = make_user(db, "OWNER").id
        db.commit()
    assert store_api.post("/api/stores", json=BODY, headers=h).status_code == 403


def test_address_error_field_is_flat(store_api, monkeypatch):
    def fail(body):
        raise ApiError(422, ErrorCode.VALIDATION_ERROR, field_errors=[
            {"field": "store.postalCode", "code": "INVALID_FORMAT", "message": "주소 확인"},
        ])
    monkeypatch.setattr(owner_stores, "verify_store_address", fail)
    r = store_api.post("/api/stores", json=BODY, headers=headers(store_api))
    assert r.json()["fieldErrors"][0]["field"] == "postalCode"
