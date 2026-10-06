import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import auth
from app.db.models import User
from app.worker_profile import router
from tests.test_worker_registration import WORKER, headers
from tests.test_worker_registration import worker_api as _worker_api

worker_api = _worker_api
PATH = "/api/users/me/profile"


@pytest.fixture
def profile_api(worker_api):
    worker_api.app.include_router(router)
    response = worker_api.post("/api/auth/registrations/workers", json=WORKER, headers=headers(worker_api))
    assert response.status_code == 201
    return worker_api


def test_get_profile_contract_and_order(profile_api):
    response = profile_api.get(PATH)
    assert response.status_code == 200
    body = response.json()
    assert {key: body[key] for key in WORKER} == WORKER
    assert body["identity"] == {"provider": "GOOGLE", "email": "worker@test.org", "emailVerified": True}
    assert body["role"] == "WORKER"
    assert response.headers["cache-control"] == "no-store"
    assert profile_api.get(PATH).json() == body


@pytest.mark.parametrize("kind,code,status", [
    ("missing", "SESSION_EXPIRED", 401), ("revoked", "SESSION_EXPIRED", 401),
    ("expired", "SESSION_EXPIRED", 401), ("registration", "REGISTRATION_REQUIRED", 401),
    ("owner", "FORBIDDEN", 403), ("suspended", "ACCOUNT_SUSPENDED", 403),
])
def test_profile_authentication(profile_api, db_engine, kind, code, status):
    from datetime import timedelta

    from app.db import utcnow
    from app.db.models import AuthSession

    with Session(db_engine) as db:
        user = db.scalar(select(User))
        session = db.scalar(select(AuthSession))
        if kind == "revoked": session.revoked_at = utcnow()
        if kind == "expired": session.expires_at = utcnow() - timedelta(seconds=1)
        if kind == "owner": user.role = "OWNER"
        if kind == "suspended": user.status = "SUSPENDED"
        if kind == "registration":
            issued = auth.create_registration_session("other-sub", "other@test.org", db=db)
        db.commit()
    if kind in {"missing", "registration"}:
        profile_api.cookies.clear()
    if kind == "registration":
        # The browser normally scopes this cookie to /api/auth; exercise the server guard directly.
        profile_api.cookies.set(auth.REGISTRATION_COOKIE_NAME, issued.token)
    response = profile_api.get(PATH)
    assert response.status_code == status
    assert response.json()["code"] == code
    assert response.headers["cache-control"] == "no-store"


def test_profile_target_is_session_owned(profile_api, db_engine):
    with Session(db_engine) as db:
        other = User(google_sub="other", google_email="private@test.org", email_verified=True,
                     role="OWNER", status="ACTIVE", name="비공개", phone_number="01099999999")
        db.add(other)
        db.commit()
        other_id = other.id
    response = profile_api.get(PATH, params={"userId": other_id})
    assert response.json()["id"] != other_id
    assert "private@test.org" not in response.text
    assert profile_api.get(f"/api/users/{other_id}/profile").status_code == 404
