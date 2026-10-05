"""Real request/transaction ordering, including cookies still in flight."""
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import auth, oauth
from app.auth_views import router as views
from app.db.models import AuthSession, OAuthTransaction, RegistrationSession
from app.errors import install_error_handlers
from app.logout import router as logout
from tests.auth_contract import ContractClient
from tests.factories import make_user
from tests.test_auth_routes import begin


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("order", ["before", "provider", "committed"])
def test_logout_cancels_callback_sessions(db_engine, monkeypatch, existing, order):
    for key, value in {
        "APP_ENV": "local", "GOOGLE_CLIENT_ID": "client", "GOOGLE_CLIENT_SECRET": "secret",
        "GOOGLE_REDIRECT_URI": "http://testserver/api/auth/google/callback",
        "FRONTEND_ORIGIN": "http://frontend.test", "ALLOWED_ORIGINS": "http://frontend.test",
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(oauth, "enforce_login_rate_limit", lambda request: None)
    if existing:
        with Session(db_engine) as db:
            make_user(db, "WORKER", google_sub="subject")
            db.commit()
    app = FastAPI()
    install_error_handlers(app)
    for router in (oauth.router, views, logout):
        app.include_router(router)
    reached, release = threading.Event(), threading.Event()
    def provider(*args):
        if order == "provider":
            reached.set()
            assert release.wait(10)
        return {"sub": "subject", "email": "subject@test.org"}
    monkeypatch.setattr(oauth, "google_identity", provider)
    setter_name = "set_session_cookie" if existing else "set_registration_cookie"
    original = getattr(auth, setter_name)
    def delayed_cookie(*args, **kwargs):
        if order == "committed":
            reached.set()
            assert release.wait(10)
        return original(*args, **kwargs)
    monkeypatch.setattr(auth, setter_name, delayed_cookie)
    with ContractClient(app, follow_redirects=False, raise_server_exceptions=False) as callback_api:
        params = begin(callback_api)
        token = callback_api.cookies.get(oauth.OAUTH_COOKIE)
        def sign_out():
            with ContractClient(app, raise_server_exceptions=False) as other:
                return other.post("/api/auth/logout", headers={
                    "Origin": "http://frontend.test",
                    "Cookie": f"{oauth.OAUTH_LOGOUT_COOKIE}={token}",
                })
        def callback():
            return callback_api.get("/api/auth/google/callback", params={"state": params["state"], "code": "code"})
        if order == "before":
            assert sign_out().status_code == 204
            response = callback()
        else:
            with ThreadPoolExecutor(max_workers=1) as executor:
                pending = executor.submit(callback)
                try:
                    assert reached.wait(10)
                    assert sign_out().status_code == 204
                finally:
                    release.set()
                response = pending.result(timeout=10)
        assert response.status_code == (302 if order == "committed" else 400)
        # Even the late 302/Set-Cookie must not grant member or signup access.
        assert callback_api.get("/api/auth/session").status_code == 401
        assert callback_api.get("/api/auth/registration").status_code == 401
    with Session(db_engine) as db:
        row = db.scalar(select(OAuthTransaction))
        assert row.cancelled_at is not None
        members = db.scalars(select(AuthSession)).all()
        signups = db.scalars(select(RegistrationSession)).all()
        assert all(s.revoked_at is not None for s in members)
        assert all(s.consumed_at is not None for s in signups)
        if order == "committed":
            assert bool(row.issued_session_id) == existing
            assert bool(row.issued_registration_id) != existing
        else:
            assert not members and not signups
