"""Actual HTTP session transitions, rejection and independent durable-state checks."""
from datetime import datetime, timedelta
from http.cookies import SimpleCookie

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import auth
from app.db import utcnow
from app.db.models import AuthSession, IdempotencyRecord, User
from app.oauth import OAUTH_COOKIE, OAUTH_LOGOUT_COOKIE
from e2e.conftest import registration_row, worker_snapshot


def test_registration_context_and_member_transition(registration, real_db):
    case = registration
    context = case.client.get("/api/auth/registration")
    assert context.status_code == 200 and context.headers["cache-control"] == "no-store"
    assert context.json()["identity"] == {"provider": "GOOGLE", "email": f"{case.subject}@e2e.test",
                                           "emailVerified": True}
    assert context.json()["allowedRoles"] == ["WORKER", "OWNER"]
    with Session(real_db) as db:
        assert datetime.fromisoformat(context.json()["expiresAt"]) == registration_row(db, case).expires_at
    denied = case.client.get("/api/auth/session")
    assert denied.status_code == 401 and denied.json()["code"] == "REGISTRATION_REQUIRED"
    old_headers = case.headers()
    registered = case.register()
    assert registered.status_code == 201
    user_id = registered.json()["user"]["id"]
    assert case.headers()["X-CSRF-Token"] != old_headers["X-CSRF-Token"]
    context = case.client.get("/api/auth/registration")
    assert context.status_code == 403 and context.json()["code"] == "ALREADY_REGISTERED"
    with Session(real_db) as db:
        before = worker_snapshot(db, user_id)
    denied = case.client.patch("/api/users/me/profile/basic", json={"name": "옛 토큰"}, headers=old_headers)
    assert denied.status_code == 403 and denied.json()["code"] == "CSRF_INVALID"
    with Session(real_db) as db:
        assert worker_snapshot(db, user_id) == before


@pytest.mark.parametrize("kind,code", [("expired", "SESSION_EXPIRED"), ("consumed", "SESSION_EXPIRED"),
                                      ("missing", "SESSION_EXPIRED"), ("unknown", "SESSION_EXPIRED"),
                                      ("unverified", "GOOGLE_IDENTITY_INVALID")])
def test_invalid_registration_session_cannot_write(registration, real_db, kind, code):
    case = registration
    headers = case.headers()
    with Session(real_db) as db:
        row = registration_row(db, case)
        if kind == "expired":
            row.expires_at = utcnow() - timedelta(seconds=1)
        elif kind == "consumed":
            row.consumed_at = utcnow()
        elif kind == "unverified":
            row.email_verified = False
        db.commit()
        before = (row.expires_at, row.consumed_at, row.email_verified)
        sessions = db.scalar(select(func.count()).select_from(AuthSession))
    if kind == "missing":
        case.client.cookies.clear()
    elif kind == "unknown":
        headers["Cookie"] = f"{auth.REGISTRATION_COOKIE_NAME}=unknown"
    context = case.client.get("/api/auth/registration", headers=headers)
    assert context.status_code == 401 and context.json()["code"] == code
    response = case.client.post("/api/auth/registrations/workers", json=case.worker, headers=headers)
    assert response.status_code == 401 and response.json()["code"] == code
    assert "set-cookie" not in response.headers
    with Session(real_db) as db:
        row = registration_row(db, case)
        assert (row.expires_at, row.consumed_at, row.email_verified) == before
        assert db.scalar(select(User).where(User.google_sub == case.subject)) is None
        assert db.scalar(select(func.count()).select_from(AuthSession)) == sessions
        assert db.scalar(select(IdempotencyRecord).where(
            IdempotencyRecord.idempotency_key == headers["Idempotency-Key"])) is None


def test_csrf_is_bound_to_current_member_and_takes_precedence(member, registrations, real_db):
    case, user_id = member
    other = registrations()
    assert other.register().status_code == 201
    foreign_headers = other.headers()
    with Session(real_db) as db:
        before = worker_snapshot(db, user_id)
    denied = case.client.patch("/api/users/me/profile/basic", json={"name": "다른 토큰"}, headers=foreign_headers)
    assert denied.status_code == 403 and denied.json()["code"] == "CSRF_INVALID"
    # A live, unconsumed registration must not override the member principal.
    pending = registrations()
    headers = case.headers()
    headers["Cookie"] = (f"{auth.SESSION_COOKIE_NAME}={case.client.cookies.get(auth.SESSION_COOKIE_NAME)}; "
                         f"{auth.REGISTRATION_COOKIE_NAME}={pending.registration_token}")
    csrf = case.client.get("/api/auth/csrf", headers=headers)
    assert csrf.status_code == 200 and csrf.json()["csrfToken"] == headers["X-CSRF-Token"]
    context = case.client.get("/api/auth/registration", headers=headers)
    assert context.status_code == 403 and context.json()["code"] == "ALREADY_REGISTERED"
    headers["X-CSRF-Token"] = pending.headers()["X-CSRF-Token"]
    denied = case.client.post("/api/auth/registrations/workers", json=case.worker, headers=headers)
    assert denied.status_code == 403 and denied.json()["code"] == "CSRF_INVALID"
    with Session(real_db) as db:
        assert worker_snapshot(db, user_id) == before
    headers["X-CSRF-Token"] = case.headers()["X-CSRF-Token"]
    duplicate = case.client.post("/api/auth/registrations/workers", json=case.worker, headers=headers)
    assert duplicate.status_code == 409 and duplicate.json()["code"] == "ALREADY_REGISTERED"
    with Session(real_db) as db:
        assert registration_row(db, pending).consumed_at is None
        assert db.scalar(select(User).where(User.google_sub == pending.subject)) is None
        assert worker_snapshot(db, user_id) == before


@pytest.mark.parametrize("kind", ["idle", "revoked", "suspended"])
def test_session_rejection_and_suspension_revocation_are_durable(member, real_db, kind):
    case, user_id = member
    with Session(real_db) as db:
        row = db.scalar(select(AuthSession).where(AuthSession.user_id == user_id))
        if kind == "idle":
            row.last_seen_at = utcnow() - auth.SESSION_IDLE_TIMEOUT - timedelta(seconds=1)
            assert row.expires_at > utcnow()
        elif kind == "revoked":
            row.revoked_at = utcnow()
        else:
            db.get(User, user_id).status = "SUSPENDED"
        db.commit()
    response = case.client.get("/api/auth/session")
    assert response.status_code == (403 if kind == "suspended" else 401)
    assert response.json()["code"] == ("ACCOUNT_SUSPENDED" if kind == "suspended" else "SESSION_EXPIRED")
    if kind == "suspended":
        with Session(real_db) as db:
            assert db.scalar(select(AuthSession).where(AuthSession.user_id == user_id)).revoked_at is not None
            db.get(User, user_id).status = "ACTIVE"
            db.commit()
        assert case.client.get("/api/auth/session").status_code == 401


def test_session_activity_refresh_commits_without_extending_absolute_limit(member, real_db):
    case, user_id = member
    stale = utcnow() - timedelta(minutes=2)
    with Session(real_db) as db:
        row = db.scalar(select(AuthSession).where(AuthSession.user_id == user_id))
        row.last_seen_at = stale
        absolute = row.expires_at
        db.commit()
    response = case.client.get("/api/auth/session")
    assert response.status_code == 200
    with Session(real_db) as db:
        row = db.scalar(select(AuthSession).where(AuthSession.user_id == user_id))
        assert row.last_seen_at > stale and row.expires_at == absolute
        refreshed = row.last_seen_at
        assert datetime.fromisoformat(response.json()["expiresAt"]) == min(absolute, refreshed + auth.SESSION_IDLE_TIMEOUT)
    assert case.client.get("/api/auth/session").status_code == 200
    with Session(real_db) as db:
        assert db.scalar(select(AuthSession).where(AuthSession.user_id == user_id)).last_seen_at == refreshed


@pytest.mark.parametrize("guard", ["Origin", "wrong-origin", "X-CSRF-Token", "wrong-csrf"])
def test_logout_guards_preserve_session(member, real_db, guard):
    case, user_id = member
    headers = case.headers()
    if guard == "wrong-origin":
        headers["Origin"] = "http://evil.test"
    elif guard == "wrong-csrf":
        headers["X-CSRF-Token"] = "invalid"
    else:
        headers.pop(guard)
    cookies = dict(case.client.cookies)
    rejected = case.client.post("/api/auth/logout", headers=headers)
    assert rejected.status_code == 403 and rejected.json()["code"] == "CSRF_INVALID"
    assert "set-cookie" not in rejected.headers and dict(case.client.cookies) == cookies
    with Session(real_db) as db:
        assert db.scalar(select(AuthSession).where(AuthSession.user_id == user_id)).revoked_at is None
    assert case.client.get("/api/auth/session").status_code == 200


def test_registration_logout_consumes_token_and_clears_exact_cookies(registration, real_db, base_url):
    case = registration
    response = case.client.post("/api/auth/logout", headers=case.headers())
    assert response.status_code == 204
    cookies = SimpleCookie()
    for value in response.headers.get_list("set-cookie"):
        cookies.load(value)
    for name, path in ((auth.SESSION_COOKIE_NAME, "/"), (auth.REGISTRATION_COOKIE_NAME, "/api/auth"),
                       (OAUTH_COOKIE, "/api/auth/google"), (OAUTH_LOGOUT_COOKIE, "/api/auth")):
        assert cookies[name]["max-age"] == "0" and cookies[name]["path"] == path
        assert cookies[name]["httponly"] and cookies[name]["samesite"].lower() == "lax"
        assert not cookies[name]["domain"] and not cookies[name]["secure"]
    assert not case.client.cookies
    with Session(real_db) as db:
        assert registration_row(db, case).consumed_at is not None
    old_cookie = {"Cookie": f"{auth.REGISTRATION_COOKIE_NAME}={case.registration_token}"}
    assert case.client.get("/api/auth/registration", headers=old_cookie).status_code == 401
    for _ in range(2):
        assert case.client.post("/api/auth/logout", headers={"Origin": base_url, **old_cookie}).status_code == 204


def test_dual_cookie_logout_uses_member_csrf_and_revokes_both(member, registrations, real_db):
    case, user_id = member
    pending = registrations()
    headers = case.headers()
    member_csrf = headers["X-CSRF-Token"]
    headers["Cookie"] = (f"{auth.SESSION_COOKIE_NAME}={case.client.cookies.get(auth.SESSION_COOKIE_NAME)}; "
                         f"{auth.REGISTRATION_COOKIE_NAME}={pending.registration_token}")
    headers["X-CSRF-Token"] = pending.headers()["X-CSRF-Token"]
    rejected = case.client.post("/api/auth/logout", headers=headers)
    assert rejected.status_code == 403 and rejected.json()["code"] == "CSRF_INVALID"
    assert "set-cookie" not in rejected.headers
    with Session(real_db) as db:
        assert db.scalar(select(AuthSession).where(AuthSession.user_id == user_id)).revoked_at is None
        assert registration_row(db, pending).consumed_at is None
    headers["X-CSRF-Token"] = member_csrf
    assert case.client.post("/api/auth/logout", headers=headers).status_code == 204
    with Session(real_db) as db:
        assert db.scalar(select(AuthSession).where(AuthSession.user_id == user_id)).revoked_at is not None
        assert registration_row(db, pending).consumed_at is not None
    assert case.client.get("/api/auth/csrf", headers={"Cookie": headers["Cookie"]}).status_code == 401


def test_logout_only_revokes_requesting_browser(member, real_db, base_url):
    case, user_id = member
    with Session(real_db) as db:
        other_session = auth.create_session(user_id, db=db)
        db.commit()
    with httpx.Client(base_url=base_url, timeout=10, trust_env=False,
                      headers={"Cookie": f"{auth.SESSION_COOKIE_NAME}={other_session.token}"}) as other:
        assert other.get("/api/auth/session").status_code == 200
        assert case.client.post("/api/auth/logout", headers=case.headers()).status_code == 204
        assert case.client.get("/api/auth/session").status_code == 401
        assert other.get("/api/auth/session").status_code == 200
    with Session(real_db) as db:
        assert db.scalar(select(AuthSession).where(AuthSession.token_hash == auth.hash_token(other_session.token))).revoked_at is None
