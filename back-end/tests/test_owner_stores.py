from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi import FastAPI
from sqlalchemy.orm import Session

from app import auth
from app.auth_views import router as auth_router
from app.db import utcnow
from app.db.models import AuthSession, StoreApprovalRequest, User
from app.errors import install_error_handlers
from app.middleware import install_middleware
from app.owner_stores import router
from tests.factories import make_store, make_user
from tests.store_contract import StoreContractClient


def add_store(db, owner, **kwargs):
    store = make_store(db, owner, **kwargs)
    approval = StoreApprovalRequest(store_id=store.id, status=store.approval_status,
                                    approved_at=store.approved_at)
    db.add(approval)
    db.flush()
    return store, approval


@pytest.fixture
def store_api(db_engine, monkeypatch):
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("ALLOWED_ORIGINS", "http://frontend.test")
    app = FastAPI()
    install_error_handlers(app)
    install_middleware(app)
    app.include_router(router)
    app.include_router(auth_router)
    with Session(db_engine) as db:
        owner = make_user(db, "OWNER")
        issued = auth.create_session(owner.id, db=db)
        db.commit()
        owner_id = owner.id
    api = StoreContractClient(app, raise_server_exceptions=False)
    api.cookies.set(auth.SESSION_COOKIE_NAME, issued.token)
    api.owner_id = owner_id
    return api


def test_owner_listing_order_pagination_and_detail(store_api, db_engine):
    now = utcnow()
    with Session(db_engine) as db:
        owner = db.get(User, store_api.owner_id)
        first, _ = add_store(db, owner, created_at=now - timedelta(seconds=1))
        a, _ = add_store(db, owner, created_at=now, approval_status="APPROVED", approved_at=now)
        b, _ = add_store(db, owner, created_at=now)
        foreign, _ = add_store(db, make_user(db, "OWNER"))
        db.commit()
        expected = sorted([a.id, b.id], reverse=True) + [first.id]
        foreign_id = foreign.id
    result = store_api.get("/api/owners/me/stores", params={"size": 2}).json()
    assert [s["id"] for s in result["items"]] == expected[:2]
    assert result["totalItems"] == 3 and result["totalPages"] == 2 and result["page"] == 0
    last = store_api.get("/api/owners/me/stores", params={"page": 1, "size": 2}).json()
    assert [s["id"] for s in last["items"]] == expected[2:]
    assert store_api.get("/api/owners/me/stores?page=2&size=2").json()["items"] == []
    pending = store_api.get(f"/api/stores/{first.id}").json()
    assert pending["permissions"] == ["READ_STORE_STATUS"] and pending["approvedAt"] is None
    assert pending["approvalRequestId"] != pending["id"]
    approved = store_api.get(f"/api/stores/{a.id}").json()
    assert len(approved["permissions"]) == 5 and approved["approvedAt"]
    for store_id in (foreign_id, str(uuid4())):
        response = store_api.get(f"/api/stores/{store_id}")
        assert response.status_code == 404 and response.json()["code"] == "STORE_NOT_FOUND"


def test_empty_listing(store_api):
    body = store_api.get("/api/owners/me/stores").json()
    assert body["items"] == [] and body["totalItems"] == body["totalPages"] == 0


@pytest.mark.parametrize("query", ["page=-1", "size=0", "size=101", "size=x", "page=1&page=2"])
def test_listing_invalid_pagination(store_api, query):
    response = store_api.get("/api/owners/me/stores?" + query)
    assert response.status_code == 422 and response.json()["code"] == "VALIDATION_ERROR"


@pytest.mark.parametrize("kind,code,status", [
    ("missing", "SESSION_EXPIRED", 401), ("expired", "SESSION_EXPIRED", 401),
    ("revoked", "SESSION_EXPIRED", 401), ("registration", "REGISTRATION_REQUIRED", 401),
    ("worker", "FORBIDDEN", 403), ("suspended", "ACCOUNT_SUSPENDED", 403),
])
def test_store_authentication(store_api, db_engine, kind, code, status):
    with Session(db_engine) as db:
        user = db.get(User, store_api.owner_id)
        row = db.query(AuthSession).one()
        if kind == "worker": user.role = "WORKER"
        if kind == "suspended": user.status = "SUSPENDED"
        if kind == "revoked": row.revoked_at = utcnow()
        if kind == "expired": row.expires_at = utcnow() - timedelta(seconds=1)
        if kind == "registration":
            issued = auth.create_registration_session("other", "other@test.org", db=db)
        db.commit()
    if kind in {"missing", "registration"}: store_api.cookies.clear()
    if kind == "registration": store_api.cookies.set(auth.REGISTRATION_COOKIE_NAME, issued.token)
    for path in ("/api/owners/me/stores", f"/api/stores/{uuid4()}"):
        response = store_api.get(path)
        assert response.status_code == status and response.json()["code"] == code
        if kind == "suspended":
            # The first rejection revokes the cookie's server session.
            status, code = 401, "SESSION_EXPIRED"


def test_invalid_store_uuid(store_api):
    assert store_api.get("/api/stores/invalid").status_code == 422
