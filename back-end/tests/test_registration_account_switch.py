"""A signup form opened for Google account A must not be submitted into account B's session.

The browser keeps one registration cookie. Logging in as B in another tab replaces it, so the
only thing a stale A form can still prove is the CSRF token it captured while A was current.
"""
import uuid
from urllib.parse import parse_qs, urlsplit

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import auth, oauth, registration
from app.db.models import RegistrationSession, Store, User
from tests.api_contract import ORIGIN

OWNER = {"name": "에이점주", "phoneNumber": "01012345678", "store": {
    "name": "에이매장", "industry": "CAFE", "postalCode": "01897", "address": "서울특별시 노원구 광운로 20",
    "businessRegistrationNumber": "1234567890", "phoneNumber": "029123456",
}}


@pytest.fixture
def google(api, monkeypatch):
    for key, value in {
        "GOOGLE_CLIENT_ID": "test-client", "GOOGLE_CLIENT_SECRET": "secret",
        "GOOGLE_REDIRECT_URI": "http://testserver/api/auth/google/callback", "FRONTEND_ORIGIN": ORIGIN,
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(oauth, "enforce_login_rate_limit", lambda request: None)
    monkeypatch.setattr(registration, "verify_store_address", lambda store: store.address)

    def login_as(sub, email):
        start = api.get("/api/auth/google", follow_redirects=False)
        state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
        monkeypatch.setattr(oauth, "google_identity", lambda code, nonce: {"sub": sub, "email": email})
        done = api.get("/api/auth/google/callback", params={"state": state, "code": "code"},
                       follow_redirects=False)
        assert done.status_code == 302 and done.headers["location"] == ORIGIN + "/__auth/signup"
    return login_as


def submit(api, csrf, body=OWNER, key=None):
    return api.post("/api/auth/registrations/owners", json=body, headers={
        "Origin": ORIGIN, "X-CSRF-Token": csrf, "Idempotency-Key": key or str(uuid.uuid4())})


def test_stale_form_token_is_rejected_after_switching_account(api, db_engine, google):
    google("sub-a", "a@test.org")
    assert api.get("/api/auth/registration").json()["identity"]["email"] == "a@test.org"
    stale = api.get("/api/auth/csrf").json()["csrfToken"]
    google("sub-b", "b@test.org")  # another tab replaces the browser's registration cookie

    r = submit(api, stale)
    assert r.status_code == 403 and r.json()["code"] == "CSRF_INVALID"
    assert "set-cookie" not in r.headers
    with Session(db_engine) as db:
        assert db.scalar(select(func.count()).select_from(User)) == 0
        assert db.scalar(select(func.count()).select_from(Store)) == 0
        rows = {row.google_sub: row for row in db.scalars(select(RegistrationSession))}
        assert rows["sub-a"].consumed_at is not None and rows["sub-b"].consumed_at is None

    # B is untouched: its own form, with its own token, still registers B exactly once.
    assert api.get("/api/auth/registration").json()["identity"]["email"] == "b@test.org"
    fresh = api.get("/api/auth/csrf").json()["csrfToken"]
    assert fresh != stale
    body = {**OWNER, "name": "비점주", "store": {**OWNER["store"], "businessRegistrationNumber": "2234567890"}}
    created = submit(api, fresh, body)
    assert created.status_code == 201, created.text
    assert created.json()["user"]["identity"]["email"] == "b@test.org"
    with Session(db_engine) as db:
        assert db.scalars(select(User.google_sub)).all() == ["sub-b"]


def test_replaced_account_cannot_resume_with_its_own_cookie(api, db_engine, google):
    google("sub-a", "a@test.org")
    old_cookie = api.cookies.get(auth.REGISTRATION_COOKIE_NAME)
    stale = api.get("/api/auth/csrf").json()["csrfToken"]
    google("sub-b", "b@test.org")
    api.cookies.set(auth.REGISTRATION_COOKIE_NAME, old_cookie)  # e.g. a restored cookie jar

    assert api.get("/api/auth/registration").json()["code"] == "SESSION_EXPIRED"
    r = submit(api, stale)
    assert r.status_code == 401 and r.json()["code"] == "SESSION_EXPIRED"
    with Session(db_engine) as db:
        assert db.scalar(select(func.count()).select_from(User)) == 0


def test_completed_signup_replays_only_with_the_member_token(api, db_engine, google):
    # A form that pins its registration token must re-read the token after the session became
    # this same user's member session (lost 201 response); the replay then needs no new write.
    google("sub-a", "a@test.org")
    pinned, key = api.get("/api/auth/csrf").json()["csrfToken"], str(uuid.uuid4())
    first = submit(api, pinned, key=key)
    assert first.status_code == 201, first.text

    stale = submit(api, pinned, key=key)
    assert stale.status_code == 403 and stale.json()["code"] == "CSRF_INVALID"
    assert api.get("/api/auth/session").json()["user"]["identity"]["email"] == "a@test.org"
    replay = submit(api, api.get("/api/auth/csrf").json()["csrfToken"], key=key)
    assert replay.status_code == 201 and replay.json() == first.json()
    assert replay.headers.get("Idempotent-Replayed") == "true" and "set-cookie" not in replay.headers
    with Session(db_engine) as db:
        assert db.scalar(select(func.count()).select_from(User)) == 1
        assert db.scalar(select(func.count()).select_from(Store)) == 1
