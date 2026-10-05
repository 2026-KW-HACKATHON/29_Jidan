from datetime import timedelta

import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import auth, oauth
from app.db import utcnow
from app.db.models import AuthSession, OAuthTransaction, RegistrationSession
from app.errors import install_error_handlers
from app.logout import router
from tests.auth_contract import ContractClient
from tests.factories import make_user


@pytest.fixture
def logout_api(db_engine, monkeypatch):
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("ALLOWED_ORIGINS", "http://frontend.test")
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(router)
    return ContractClient(app, raise_server_exceptions=False)


def test_logout_all_browser_sessions_and_repeat(logout_api, db_engine):
    with Session(db_engine) as db:
        user = make_user(db, "WORKER")
        member = auth.create_session(user.id, db=db)
        other_browser = auth.create_session(user.id, db=db)
        reg = auth.create_registration_session("new", "new@test.org", db=db)
        db.add(OAuthTransaction(token_hash=auth.hash_token("oauth-token"), state_hash="s" * 64,
                                nonce_hash="n" * 64, expires_at=utcnow() + timedelta(minutes=5)))
        db.commit()
    logout_api.cookies.set(auth.SESSION_COOKIE_NAME, member.token)
    logout_api.cookies.set(auth.REGISTRATION_COOKIE_NAME, reg.token)
    logout_api.cookies.set(oauth.OAUTH_LOGOUT_COOKIE, "oauth-token")
    r = logout_api.post("/api/auth/logout", headers={"Origin": "http://frontend.test", "X-CSRF-Token": member.csrf_token})
    assert r.status_code == 204 and r.content == b""
    assert len(r.headers.get_list("set-cookie")) == 4
    with Session(db_engine) as db:
        assert db.scalar(select(AuthSession).where(AuthSession.token_hash == auth.hash_token(member.token))).revoked_at
        assert db.scalar(select(AuthSession).where(AuthSession.token_hash == auth.hash_token(other_browser.token))).revoked_at is None
        assert db.scalar(select(RegistrationSession)).consumed_at
        assert db.scalar(select(OAuthTransaction)).cancelled_at
    assert logout_api.post("/api/auth/logout", headers={"Origin": "http://frontend.test"}).status_code == 204


@pytest.mark.parametrize("kind", ["member", "registration", "both"])
def test_live_session_requires_csrf(logout_api, db_engine, kind):
    with Session(db_engine) as db:
        issued = auth.create_session(make_user(db, "WORKER").id, db=db)
        reg = auth.create_registration_session("new", "new@test.org", db=db)
        db.commit()
    if kind in {"member", "both"}: logout_api.cookies.set(auth.SESSION_COOKIE_NAME, issued.token)
    if kind in {"registration", "both"}: logout_api.cookies.set(auth.REGISTRATION_COOKIE_NAME, reg.token)
    assert logout_api.post("/api/auth/logout", headers={"Origin": "http://frontend.test"}).status_code == 403
    good = issued.csrf_token if kind in {"member", "both"} else reg.csrf_token
    assert logout_api.post("/api/auth/logout", headers={"Origin": "http://frontend.test", "X-CSRF-Token": good}).status_code == 204


def test_anonymous_still_checks_origin(logout_api):
    assert logout_api.post("/api/auth/logout").status_code == 403
    assert logout_api.post("/api/auth/logout", headers={"Origin": "http://evil.test"}).status_code == 403
    assert logout_api.post("/api/auth/logout", headers={"Origin": "http://frontend.test"}).status_code == 204


def test_expired_session_needs_only_origin(logout_api, db_engine):
    with Session(db_engine) as db:
        issued = auth.create_session(make_user(db, "WORKER").id, db=db)
        db.scalar(select(AuthSession)).expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
    logout_api.cookies.set(auth.SESSION_COOKIE_NAME, issued.token)
    assert logout_api.post("/api/auth/logout", headers={"Origin": "http://frontend.test"}).status_code == 204
