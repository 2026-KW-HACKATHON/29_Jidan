"""Auth HTTP → MySQL scenarios; external Google verification is the seed boundary."""
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import auth
from app.db import utcnow
from app.db.models import (
    AuthSession,
    AvailabilityDay,
    AvailabilityRule,
    IdempotencyRecord,
    User,
    WorkerProfile,
)
from e2e.conftest import registration_row


def test_worker_registration_persists_entire_aggregate_and_replays(registration, real_db):
    case = registration
    key = case.headers()["Idempotency-Key"]
    response = case.register(key=key)
    assert response.status_code == 201, response.text
    assert response.headers["cache-control"] == "no-store"
    assert "HttpOnly" in response.headers["set-cookie"]
    user_id = response.json()["user"]["id"]
    token = case.client.cookies.get(auth.SESSION_COOKIE_NAME)
    assert token and response.json()["nextAction"] == "WORKER_HOME"
    with Session(real_db) as db:
        user = db.get(User, user_id)
        assert user.google_sub == case.subject and user.name == case.worker["name"]
        assert db.get(WorkerProfile, user_id).experience_level == "NEW"
        rule = db.scalar(select(AvailabilityRule).where(AvailabilityRule.worker_id == user_id))
        assert rule.start_time.hour == 9 and rule.end_time.hour == 14
        assert db.scalar(select(AvailabilityDay.weekday).where(AvailabilityDay.rule_id == rule.id)) == "MON"
        assert registration_row(db, case).consumed_at is not None
        assert db.scalar(select(AuthSession).where(AuthSession.user_id == user_id)).token_hash == auth.hash_token(token)
        assert db.scalar(select(IdempotencyRecord).where(IdempotencyRecord.idempotency_key == key)) is not None
    assert case.client.get("/api/auth/session").json()["user"]["id"] == user_id
    replay = case.register(key=key)
    assert replay.status_code == 201 and replay.json() == response.json()
    assert replay.headers["Idempotent-Replayed"] == "true" and "set-cookie" not in replay.headers
    changed = case.register(body={**case.worker, "name": "다른 이름"}, key=key)
    assert changed.status_code == 409 and changed.json()["code"] == "IDEMPOTENCY_KEY_REUSED"
    duplicate = case.register()
    assert duplicate.status_code == 409 and duplicate.json()["code"] == "ALREADY_REGISTERED"
    with Session(real_db) as db:
        assert db.scalar(select(func.count()).select_from(User).where(User.google_sub == case.subject)) == 1
        assert db.scalar(select(func.count()).select_from(AuthSession).where(AuthSession.user_id == user_id)) == 1
        assert db.get(User, user_id).name == case.worker["name"]


@pytest.mark.parametrize("change", [
    {"name": " "}, {"birthDate": "2001-02-29"}, {"birthDate": "2999-01-01"},
    {"phoneNumber": "010１２３４５６７８"}, {"role": "OWNER"},
    {"experienceLevel": "EXPERIENCED"}, {"availabilities": []},
])
def test_invalid_registration_preserves_db_and_can_retry(registration, real_db, change):
    case = registration
    key = case.headers()["Idempotency-Key"]
    response = case.register(body={**case.worker, **change}, key=key)
    assert response.status_code == 422 and response.json()["code"] == "VALIDATION_ERROR"
    assert "set-cookie" not in response.headers
    with Session(real_db) as db:
        assert db.scalar(select(User).where(User.google_sub == case.subject)) is None
        assert registration_row(db, case).consumed_at is None
        assert db.scalar(select(IdempotencyRecord).where(IdempotencyRecord.idempotency_key == key)) is None
    assert case.register(key=key).status_code == 201


@pytest.mark.parametrize("guard,status", [("Origin", 403), ("X-CSRF-Token", 403),
                                         ("Idempotency-Key", 422), ("wrong-origin", 403),
                                         ("wrong-csrf", 403), ("malformed-key", 422)])
def test_registration_guards_do_not_consume_session(registration, real_db, guard, status):
    headers = registration.headers()
    if guard == "wrong-origin": headers["Origin"] = "http://evil.test"
    elif guard == "wrong-csrf": headers["X-CSRF-Token"] = "invalid"
    elif guard == "malformed-key": headers["Idempotency-Key"] = "invalid"
    else: headers.pop(guard)
    response = registration.client.post("/api/auth/registrations/workers",
                                        json=registration.worker, headers=headers)
    assert response.status_code == status
    with Session(real_db) as db:
        assert db.scalar(select(User).where(User.google_sub == registration.subject)) is None
        assert registration_row(db, registration).consumed_at is None


def test_logout_revokes_persisted_session_and_old_cookie(member, real_db, base_url):
    case, user_id = member
    token = case.client.cookies.get(auth.SESSION_COOKIE_NAME)
    headers = case.headers()
    response = case.client.post("/api/auth/logout", headers=headers)
    assert response.status_code == 204 and auth.SESSION_COOKIE_NAME not in case.client.cookies
    with Session(real_db) as db:
        assert db.scalar(select(AuthSession).where(AuthSession.user_id == user_id)).revoked_at is not None
    with httpx.Client(base_url=base_url, timeout=10, trust_env=False) as stale:
        stale.cookies.set(auth.SESSION_COOKIE_NAME, token)
        rejected = stale.get("/api/auth/session")
        assert rejected.status_code == 401 and rejected.json()["code"] == "SESSION_EXPIRED"
    assert case.client.post("/api/auth/logout", headers={"Origin": base_url}).status_code == 204


@pytest.mark.parametrize("kind,code,status", [("expired", "SESSION_EXPIRED", 401),
                                              ("suspended", "ACCOUNT_SUSPENDED", 403)])
def test_db_session_state_is_enforced_over_http(member, real_db, kind, code, status):
    case, user_id = member
    with Session(real_db) as db:
        if kind == "expired":
            row = db.scalar(select(AuthSession).where(AuthSession.user_id == user_id))
            row.expires_at = utcnow() - timedelta(seconds=1)
        else:
            db.get(User, user_id).status = "SUSPENDED"
        db.commit()
    response = case.client.get("/api/auth/session")
    assert response.status_code == status and response.json()["code"] == code
