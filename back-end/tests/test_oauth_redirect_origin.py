"""OAuth redirect targets are ASCII origins; a Unicode setting fails before any session exists."""
from urllib.parse import parse_qs, urlsplit

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import auth, oauth
from app.csrf import normalize_origin
from app.db.models import AuthSession, OAuthTransaction, RegistrationSession
from tests.api_contract import ORIGIN

UNICODE_ORIGIN = "http://지단.test"
PUNYCODE_ORIGIN = "http://xn--2e0b73w.test"


@pytest.fixture
def google(api, monkeypatch):
    for key, value in {
        "GOOGLE_CLIENT_ID": "test-client", "GOOGLE_CLIENT_SECRET": "secret",
        "GOOGLE_REDIRECT_URI": "http://testserver/api/auth/google/callback", "FRONTEND_ORIGIN": ORIGIN,
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(oauth, "enforce_login_rate_limit", lambda request: None)
    monkeypatch.setattr(oauth, "google_identity", lambda code, nonce: {"sub": "new", "email": "n@test.org"})
    return monkeypatch


def start(api):
    return api.get("/api/auth/google", follow_redirects=False)


def callback(api, state):
    return api.get("/api/auth/google/callback", params={"state": state, "code": "code"},
                   follow_redirects=False)


def counts(engine):
    with Session(engine) as db:
        return tuple(db.scalar(select(func.count()).select_from(model))
                     for model in (OAuthTransaction, RegistrationSession, AuthSession))


@pytest.mark.parametrize("key,value", [
    ("FRONTEND_ORIGIN", UNICODE_ORIGIN),
    ("GOOGLE_REDIRECT_URI", "http://testserver/api/auth/google/콜백"),
    ("GOOGLE_REDIRECT_URI", "http://지단.test/api/auth/google/callback"),
])
def test_unicode_setting_rejects_login_start_without_writes(api, db_engine, google, key, value):
    google.setenv(key, value)
    r = start(api)
    assert r.status_code == 500 and r.json()["code"] == "INTERNAL_ERROR"
    assert "location" not in r.headers and "set-cookie" not in r.headers
    assert counts(db_engine) == (0, 0, 0)


@pytest.mark.parametrize("member", [False, True])
def test_unicode_frontend_origin_at_callback_issues_nothing(api, db_engine, google, member):
    state = parse_qs(urlsplit(start(api).headers["location"]).query)["state"][0]
    google.setenv("FRONTEND_ORIGIN", UNICODE_ORIGIN)  # configuration changed mid-login
    google.setattr(oauth, "google_identity", lambda *args: pytest.fail("provider called"))
    if member:
        from tests.factories import make_user
        with Session(db_engine) as db:
            make_user(db, "WORKER", google_sub="new")
            db.commit()
    r = callback(api, state)
    assert r.status_code == 500 and r.json()["code"] == "INTERNAL_ERROR"
    assert "location" not in r.headers
    assert auth.REGISTRATION_COOKIE_NAME not in api.cookies and auth.SESSION_COOKIE_NAME not in api.cookies
    assert oauth.OAUTH_COOKIE not in api.cookies
    with Session(db_engine) as db:
        row = db.scalar(select(OAuthTransaction))
        assert row.consumed_at is not None and row.cancelled_at is not None
        assert row.issued_registration_id is None and row.issued_session_id is None
    assert counts(db_engine)[1:] == (0, 0)


def test_ascii_idn_origin_is_used_verbatim(api, db_engine, google):
    google.setenv("FRONTEND_ORIGIN", PUNYCODE_ORIGIN + "/")
    state = parse_qs(urlsplit(start(api).headers["location"]).query)["state"][0]
    r = callback(api, state)
    assert r.status_code == 302 and r.headers["location"] == PUNYCODE_ORIGIN + "/__auth/signup"
    assert counts(db_engine)[1:] == (1, 0)


@pytest.mark.parametrize("value,expected", [
    (UNICODE_ORIGIN, None), ("http://jidan.tést", None), ("http://frontend.test ", None),
    (PUNYCODE_ORIGIN, PUNYCODE_ORIGIN + ":80"),
])
def test_normalize_origin_accepts_ascii_only(value, expected):
    assert normalize_origin(value) == expected


def test_unicode_allowed_origin_entry_is_ignored(api, monkeypatch):
    monkeypatch.setenv("ALLOWED_ORIGINS", f"{UNICODE_ORIGIN},{ORIGIN}")
    from app.csrf import allowed_origins
    assert allowed_origins() == frozenset({ORIGIN + ":80"})
