"""Every profile route enforces its own guard and preserves the whole aggregate."""
from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import auth
from app.db import utcnow
from app.db.models import AuthSession, User
from e2e.conftest import worker_snapshot
from e2e.test_profile_http import CAREER, PATH, profile_headers

WRITES = [("PATCH", "/basic", {"name": "접근 거부"}),
          ("PUT", "/careers", {"experienceLevel": "EXPERIENCED", "careers": [CAREER]}),
          ("PUT", "/availabilities", {"availabilities": [
              {"days": ["TUE"], "startTime": "10:00", "endTime": "11:00", "endsNextDay": False}]})]


@pytest.mark.parametrize("method,suffix,body", [("GET", "", None), *WRITES])
@pytest.mark.parametrize("kind,status,code", [
    ("missing", 401, "SESSION_EXPIRED"), ("expired", 401, "SESSION_EXPIRED"),
    ("revoked", 401, "SESSION_EXPIRED"), ("registration", 401, "REGISTRATION_REQUIRED"),
    ("owner", 403, "FORBIDDEN"), ("suspended", 403, "ACCOUNT_SUSPENDED"),
])
def test_all_profile_routes_reject_invalid_principals(member, registrations, real_db,
                                                      method, suffix, body, kind, status, code):
    case, user_id = member
    headers = profile_headers(case)
    with Session(real_db) as db:
        session = db.scalar(select(AuthSession).where(AuthSession.user_id == user_id))
        if kind == "expired":
            session.expires_at = utcnow() - timedelta(seconds=1)
        elif kind == "revoked":
            session.revoked_at = utcnow()
        elif kind == "owner":
            db.get(User, user_id).role = "OWNER"
        elif kind == "suspended":
            db.get(User, user_id).status = "SUSPENDED"
        db.commit()
        before = worker_snapshot(db, user_id)
    if kind == "missing":
        case.client.cookies.clear()
    elif kind == "registration":
        pending = registrations()
        # Explicit Cookie exercises the server guard; browsers scope this to /api/auth.
        headers["Cookie"] = f"{auth.REGISTRATION_COOKIE_NAME}={pending.registration_token}"
        headers["X-CSRF-Token"] = pending.headers()["X-CSRF-Token"]
    response = case.client.request(method, PATH + suffix, json=body, headers=headers)
    assert response.status_code == status and response.json()["code"] == code
    assert response.headers["cache-control"] == "no-store" and "set-cookie" not in response.headers
    with Session(real_db) as db:
        assert worker_snapshot(db, user_id) == before


@pytest.mark.parametrize("method,suffix,body", WRITES)
@pytest.mark.parametrize("guard", ["Origin", "wrong-origin", "null-origin", "duplicate-origin",
                                   "X-CSRF-Token", "wrong-csrf"])
def test_all_profile_writes_reject_origin_and_csrf(member, real_db, method, suffix, body, guard):
    case, user_id = member
    headers = profile_headers(case)
    if guard == "wrong-origin":
        headers["Origin"] = "http://evil.test"
    elif guard == "null-origin":
        headers["Origin"] = "null"
    elif guard == "duplicate-origin":
        headers = [*headers.items(), ("Origin", headers["Origin"])]
    elif guard == "wrong-csrf":
        headers["X-CSRF-Token"] = "invalid"
    else:
        headers.pop(guard)
    with Session(real_db) as db:
        before = worker_snapshot(db, user_id)
    response = case.client.request(method, PATH + suffix, json=body, headers=headers)
    assert response.status_code == 403 and response.json()["code"] == "CSRF_INVALID"
    assert response.headers["cache-control"] == "no-store" and "set-cookie" not in response.headers
    with Session(real_db) as db:
        assert worker_snapshot(db, user_id) == before


def test_two_worker_profiles_remain_isolated_during_all_writes(member, registrations, real_db):
    case, user_id = member
    other = registrations()
    other.worker.update(name="다른 근무자", experienceLevel="EXPERIENCED", careers=[CAREER],
                        availabilities=[{"days": ["WED", "FRI"], "startTime": "15:00",
                                         "endTime": "18:00", "endsNextDay": False}])
    registered = other.register()
    assert registered.status_code == 201
    other_id = registered.json()["user"]["id"]
    other_profile = other.client.get(PATH).json()
    with Session(real_db) as db:
        saved = worker_snapshot(db, other_id)
    queried = case.client.get(PATH, params={"userId": other_id})
    assert queried.status_code == 200 and queried.json()["id"] == user_id
    assert other_profile["identity"]["email"] not in queried.text
    assert case.client.get(f"/api/users/{other_id}/profile").status_code == 404
    with Session(real_db) as db:
        before_injection = worker_snapshot(db, user_id)
    injected = case.client.patch(PATH + "/basic", json={"name": "침범", "userId": other_id},
                                  headers=profile_headers(case))
    assert injected.status_code == 422
    with Session(real_db) as db:
        assert worker_snapshot(db, user_id) == before_injection
        assert worker_snapshot(db, other_id) == saved
    assert other.client.get(PATH).json() == other_profile
    for method, suffix, body in WRITES:
        rejected = case.client.request(method, PATH + suffix, json=body, headers=profile_headers(other))
        assert rejected.status_code == 403 and rejected.json()["code"] == "CSRF_INVALID"
        assert case.client.get(PATH).json() == queried.json()
        assert other.client.get(PATH).json() == other_profile
        with Session(real_db) as db:
            assert worker_snapshot(db, user_id) == before_injection
            assert worker_snapshot(db, other_id) == saved
    for method, suffix, body in WRITES:
        response = case.client.request(method, PATH + suffix, json=body, headers=profile_headers(case))
        assert response.status_code == 200 and response.json()["id"] == user_id
        assert {field_name: response.json()[field_name] for field_name in body} == body
        assert case.client.get(PATH).json() == response.json()
        assert other.client.get(PATH).json() == other_profile
        with Session(real_db) as db:
            assert worker_snapshot(db, other_id) == saved
    # Clear A's lists as well: deletion queries must not erase B's rows.
    cleared = case.client.put(PATH + "/careers", json={"experienceLevel": "NEW", "careers": []},
                               headers=profile_headers(case))
    assert cleared.status_code == 200 and cleared.json()["careers"] == []
    assert cleared.json()["experienceLevel"] == "NEW" and case.client.get(PATH).json() == cleared.json()
    assert other.client.get(PATH).json() == other_profile
    with Session(real_db) as db:
        assert worker_snapshot(db, other_id) == saved
