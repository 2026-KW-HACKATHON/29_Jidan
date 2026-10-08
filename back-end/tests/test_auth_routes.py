from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import auth, oauth
from app.auth_views import router as views_router
from app.db import engine as engine_module
from app.db import utcnow
from app.db.models import AuthSession, OAuthTransaction
from app.errors import install_error_handlers
from app.middleware import install_middleware
from tests.auth_contract import ContractClient
from tests.factories import make_user


@pytest.fixture
def auth_api(engine, monkeypatch):
    monkeypatch.setattr(engine_module, "get_engine", lambda: engine)
    for key, value in {
        "APP_ENV": "local", "GOOGLE_CLIENT_ID": "test-client", "GOOGLE_CLIENT_SECRET": "secret",
        "GOOGLE_REDIRECT_URI": "http://testserver/api/auth/google/callback",
        "FRONTEND_ORIGIN": "http://frontend.test", "ALLOWED_ORIGINS": "http://frontend.test",
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(oauth, "enforce_login_rate_limit", lambda request: None)
    app = FastAPI()
    install_error_handlers(app)
    install_middleware(app)
    app.include_router(oauth.router)
    app.include_router(views_router)
    return ContractClient(app, follow_redirects=False, raise_server_exceptions=False)


def begin(api):
    r = api.get("/api/auth/google")
    assert r.status_code == 302
    return {key: values[0] for key, values in parse_qs(urlsplit(r.headers["location"]).query).items()}


def test_new_identity(auth_api, engine, monkeypatch):
    params = begin(auth_api)
    monkeypatch.setattr(oauth, "google_identity", lambda code, nonce: {"sub": "new", "email": "new@test.org"})
    r = auth_api.get("/api/auth/google/callback", params={"state": params["state"], "code": "secret-code"})
    assert r.status_code == 302 and r.headers["location"] == "http://frontend.test/__auth/signup"
    assert auth_api.get("/api/auth/registration").json()["allowedRoles"] == ["WORKER", "OWNER"]
    assert auth_api.get("/api/auth/session").json()["code"] == "REGISTRATION_REQUIRED"
    assert auth_api.get("/api/auth/csrf").status_code == 200
    with Session(engine) as db:
        row = db.scalar(select(OAuthTransaction))
        assert row.consumed_at and row.state_hash != params["state"] and row.nonce_hash != params["nonce"]


def test_existing_member_rotates(auth_api, engine, monkeypatch):
    with Session(engine) as db:
        issued = auth.create_session(make_user(db, "WORKER", google_sub="existing").id, db=db)
        db.commit()
    auth_api.cookies.set(auth.SESSION_COOKIE_NAME, issued.token)
    params = begin(auth_api)
    monkeypatch.setattr(oauth, "google_identity", lambda code, nonce: {"sub": "existing", "email": "updated@test.org"})
    r = auth_api.get("/api/auth/google/callback", params={"state": params["state"], "code": "code"})
    assert r.headers["location"] == "http://frontend.test/__auth/session"
    assert auth_api.get("/api/auth/session").json()["user"]["identity"]["email"] == "updated@test.org"
    assert auth_api.get("/api/auth/registration").status_code == 403
    assert auth_api.get("/api/auth/registration").json()["code"] == "ALREADY_REGISTERED"
    with Session(engine) as db:
        assert db.scalar(select(AuthSession).where(AuthSession.token_hash == auth.hash_token(issued.token))).revoked_at


@pytest.mark.parametrize("failure", ["state", "expired", "binding", "replay"])
def test_invalid_transaction(auth_api, engine, monkeypatch, failure):
    params = begin(auth_api)
    token = auth_api.cookies.get(oauth.OAUTH_COOKIE)
    if failure == "expired":
        with Session(engine) as db:
            db.scalar(select(OAuthTransaction)).expires_at = utcnow() - timedelta(seconds=1)
            db.commit()
    elif failure == "binding":
        auth_api.cookies.clear()
    elif failure == "state":
        params["state"] = "wrong" * 9
    elif failure == "replay":
        auth_api.get("/api/auth/google/callback", params={"state": params["state"], "error": "access_denied"})
        auth_api.cookies.set(oauth.OAUTH_COOKIE, token)
    monkeypatch.setattr(oauth, "google_identity", lambda *args: pytest.fail("provider called"))
    r = auth_api.get("/api/auth/google/callback", params={"state": params["state"], "code": "code"})
    assert r.status_code == 400 and r.json()["code"] == "OAUTH_STATE_INVALID"
    assert r.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("extra", [[], [("code", "a"), ("error", "b")], [("code", "a"), ("code", "b")], [("code", "")]])
def test_malformed_callback(auth_api, extra):
    params = begin(auth_api)
    r = auth_api.get("/api/auth/google/callback", params=[("state", params["state"]), *extra])
    assert r.status_code == 400 and r.json()["code"] == "INVALID_REQUEST"


def test_cancel(auth_api):
    params = begin(auth_api)
    r = auth_api.get("/api/auth/google/callback", params={"state": params["state"], "error": "access_denied", "error_description": "SECRET"})
    assert r.headers["location"] == "http://frontend.test/login?error=GOOGLE_ACCESS_DENIED"
    assert "SECRET" not in r.text + str(r.headers)


@pytest.mark.parametrize("change", [{"nonce": "wrong"}, {"email_verified": False}, {"email_verified": "true"}, {"sub": ""}, {"email": "invalid"}, {"azp": "wrong-client"}])
def test_claims_validation(auth_api, monkeypatch, change):
    class FakeClient:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def post(self, *args, **kwargs):
            import httpx
            return httpx.Response(200, json={"id_token": "secret-id-token"})
    monkeypatch.setattr(oauth.httpx, "Client", FakeClient)
    claims = {"nonce": "nonce", "sub": "subject", "email": "email@test.org", "email_verified": True, **change}
    monkeypatch.setattr(oauth, "verify_oauth2_token", lambda *args, **kwargs: claims)
    with pytest.raises(oauth.ApiError) as exc:
        oauth.google_identity("code", auth.hash_token("nonce"))
    assert exc.value.code == "GOOGLE_IDENTITY_INVALID"


@pytest.mark.parametrize("change,valid", [
    ({}, True), ({"aud": "other-client"}, False), ({"iss": "https://evil.test"}, False),
    ({"exp": 1}, False), ({"nonce": "wrong"}, False), ({"email_verified": False}, False),
])
def test_real_signed_id_token(auth_api, monkeypatch, change, valid):
    import base64
    import json
    import time

    import httpx
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding, rsa

    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    claims = {"iss": "https://accounts.google.com", "aud": "test-client", "iat": int(time.time()),
              "exp": int(time.time()) + 60, "sub": "subject", "nonce": "nonce",
              "email": "email@test.org", "email_verified": True, **change}
    def encoded(value):
        return base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=")
    data = encoded({"alg": "RS256", "kid": "key"}) + b"." + encoded(claims)
    signature = private.sign(data, padding.PKCS1v15(), hashes.SHA256())
    token = (data + b"." + base64.urlsafe_b64encode(signature).rstrip(b"=")).decode()
    class FakeClient:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def post(self, *args, **kwargs): return httpx.Response(200, json={"id_token": token})
    class Transport:
        session = type("Session", (), {"close": lambda self: None})()
        def __call__(self, *args, **kwargs):
            pem = private.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
            return type("Response", (), {"status": 200, "data": json.dumps({"key": pem}).encode()})()
    monkeypatch.setattr(oauth.httpx, "Client", FakeClient)
    monkeypatch.setattr(oauth, "BoundedGoogleRequest", Transport)
    if valid:
        assert oauth.google_identity("code", auth.hash_token("nonce"))["sub"] == "subject"
    else:
        with pytest.raises(oauth.ApiError) as exc:
            oauth.google_identity("code", auth.hash_token("nonce"))
        assert exc.value.code == "GOOGLE_IDENTITY_INVALID"


def test_unexpected_callback_error_clears_login_cookies(auth_api, engine, monkeypatch):
    params = begin(auth_api)
    def fail(*args): raise RuntimeError("provider-secret")
    monkeypatch.setattr(oauth, "google_identity", fail)
    r = auth_api.get("/api/auth/google/callback", params={"state": params["state"], "code": "CODE"})
    assert r.status_code == 500 and "provider-secret" not in r.text
    assert oauth.OAUTH_COOKIE not in auth_api.cookies
    assert oauth.OAUTH_LOGOUT_COOKIE not in auth_api.cookies
    with Session(engine) as db:
        assert db.scalar(select(OAuthTransaction)).consumed_at


def test_suspended_google_account_cannot_login(auth_api, engine, monkeypatch):
    with Session(engine) as db:
        user = make_user(db, "WORKER", google_sub="suspended", status="SUSPENDED")
        auth.create_session(user.id, db=db)
        db.commit()
    params = begin(auth_api)
    monkeypatch.setattr(oauth, "google_identity", lambda *args: {"sub": "suspended", "email": "s@test.org"})
    r = auth_api.get("/api/auth/google/callback", params={"state": params["state"], "code": "CODE"})
    assert r.status_code == 403 and r.json()["code"] == "ACCOUNT_SUSPENDED"
    assert auth.SESSION_COOKIE_NAME not in auth_api.cookies
    with Session(engine) as db:
        assert db.scalar(select(AuthSession)).revoked_at


def test_suspended_member_cannot_read_a_csrf_token(api, db_engine):
    """getCsrfToken 403: a live cookie of an account suspended after login is refused with
    ACCOUNT_SUSPENDED and its session is revoked (no token for a suspended account)."""
    from app.db.models import User
    from tests.api_contract import login

    with Session(db_engine) as db:
        user_id = make_user(db, "WORKER").id
        db.commit()
    member = login(api, user_id)
    assert api.get("/api/auth/csrf").json()["csrfToken"] == member.csrf_token
    with Session(db_engine) as db:
        db.get(User, user_id).status = "SUSPENDED"
        db.commit()
    response = api.get("/api/auth/csrf")
    assert (response.status_code, response.json()["code"]) == (403, "ACCOUNT_SUSPENDED")
    assert "csrfToken" not in response.json()
    with Session(db_engine) as db:
        assert db.scalars(select(AuthSession).where(AuthSession.user_id == user_id)).one().revoked_at is not None
