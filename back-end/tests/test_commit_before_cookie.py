"""Commit-before-response behaviour of the session helpers and the idempotency path (#104).

A request's 2xx and its `Set-Cookie` must never be sent for a transaction that then fails to
commit (the common rule #103 settles for `get_session`).
"""

import uuid

import pytest
from fastapi import FastAPI, Response
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.auth import (
    REGISTRATION_COOKIE_NAME,
    DbSession,
    commit_then_clear_session_cookie,
    commit_then_set_registration_cookie,
    commit_then_set_session_cookie,
    create_registration_session,
    create_session,
    set_session_cookie,
)
from app.csrf import CsrfRegistration
from app.db.models import AuthSession, IdempotencyRecord, RegistrationSession, User
from app.errors import install_error_handlers
from app.idempotency import IdempotencyKey, IdempotentResult, run_idempotent
from tests.factories import make_user

ORIGIN = "https://app.example.com"


def build_app() -> FastAPI:
    app = FastAPI()
    install_error_handlers(app)

    @app.post("/api/t/login/{user_id}")
    def login(user_id: str, db: DbSession, response: Response):
        commit_then_set_session_cookie(db, response, create_session(user_id, db=db))
        return {"ok": True}

    @app.post("/api/t/login-bare/{user_id}")
    def login_bare(user_id: str, db: DbSession, response: Response):
        set_session_cookie(response, create_session(user_id, db=db))  # commit comes later
        return {"ok": True}

    @app.post("/api/t/logout/{token}")
    def logout(token: str, db: DbSession, response: Response):
        from app.auth import revoke_session
        revoke_session(token, db=db)
        commit_then_clear_session_cookie(db, response)
        return {"ok": True}

    @app.post("/api/t/google-callback")
    def callback(db: DbSession, response: Response):
        issued = create_registration_session("sub-x", "x@example.com", db=db)
        commit_then_set_registration_cookie(db, response, issued)
        return {"ok": True}

    @app.post("/api/t/register")
    def register(caller: CsrfRegistration, db: DbSession, key: IdempotencyKey):
        issued_box = {}

        def work() -> IdempotentResult:
            user = make_user(db, "WORKER", google_sub=caller.google_sub)
            issued_box["issued"] = create_session(user.id, db=db)
            return IdempotentResult(201, {"userId": user.id})

        response = run_idempotent(
            db=db, principal=caller, key=key, method="POST", path="/api/t/register",
            body={}, handler=work,
        )
        set_session_cookie(response, issued_box["issued"])
        return response

    return app


@pytest.fixture(autouse=True)
def environment(monkeypatch):
    monkeypatch.setenv("ALLOWED_ORIGINS", ORIGIN)


@pytest.fixture
def api(db_engine):
    return TestClient(build_app(), raise_server_exceptions=False)


def count(engine, model) -> int:
    with Session(engine) as session:
        return session.scalar(select(func.count()).select_from(model))


@event.listens_for(Session, "after_flush")
def _mark_business_writes(session, _context):
    if any(isinstance(o, (User, AuthSession, RegistrationSession)) for o in session.new):
        session.info["jidan_touched"] = True


def break_commits(monkeypatch, *, skip: int = 0):
    """Make the request session's commit fail after `skip` successful ones.

    Only sessions holding a User or AuthSession (the request's own business writes) are
    affected, not the short bookkeeping transactions of the idempotency layer.
    """
    real = Session.commit
    state = {"n": 0}

    def commit(self):
        if self.info.get("jidan_touched"):
            state["n"] += 1
        else:
            return real(self)
        if state["n"] > skip:
            raise OperationalError("COMMIT", {}, Exception("commit failed"))
        return real(self)

    monkeypatch.setattr(Session, "commit", commit)
    return lambda: monkeypatch.setattr(Session, "commit", real)



def new_user(engine) -> str:
    with Session(engine) as session:
        user_id = make_user(session, "WORKER").id
        session.commit()
    return user_id


def test_failed_commit_of_a_new_session_gives_no_2xx_and_no_cookie(api, db_engine, monkeypatch):
    user_id = new_user(db_engine)
    restore = break_commits(monkeypatch)
    response = api.post(f"/api/t/login/{user_id}")
    restore()
    assert response.status_code == 500
    assert "set-cookie" not in response.headers
    assert count(db_engine, AuthSession) == 0


def test_successful_login_sets_the_cookie_after_the_row_exists(api, db_engine):
    response = api.post(f"/api/t/login/{new_user(db_engine)}")
    assert response.status_code == 200 and "jidan_session=" in response.headers["set-cookie"]
    assert count(db_engine, AuthSession) == 1


def test_failed_commit_of_a_registration_session_sets_no_cookie(api, db_engine, monkeypatch):
    restore = break_commits(monkeypatch)
    response = api.post("/api/t/google-callback")
    restore()
    assert response.status_code == 500 and "set-cookie" not in response.headers
    assert count(db_engine, RegistrationSession) == 0
    ok = api.post("/api/t/google-callback")
    assert ok.status_code == 200 and REGISTRATION_COOKIE_NAME in ok.headers["set-cookie"]


def test_failed_commit_of_a_logout_does_not_clear_the_cookie_or_revoke(api, db_engine, monkeypatch):
    user_id = new_user(db_engine)
    issued = create_session(user_id)
    # revocation updates an existing row, so every commit fails here, not only flagged ones
    orig = Session.commit

    def failing(self):
        raise OperationalError("COMMIT", {}, Exception("commit failed"))

    monkeypatch.setattr(Session, "commit", failing)
    response = api.post(f"/api/t/logout/{issued.token}")
    monkeypatch.setattr(Session, "commit", orig)
    assert response.status_code == 500 and "set-cookie" not in response.headers
    with Session(db_engine) as session:
        assert session.scalars(select(AuthSession)).one().revoked_at is None


def test_failed_commit_in_the_idempotent_path_gives_no_2xx_cookie_or_record(
    api, db_engine, monkeypatch,
):
    headers = registration_headers()
    restore = break_commits(monkeypatch)
    response = api.post("/api/t/register", json={}, headers=headers)
    restore()
    assert response.status_code == 500
    assert "set-cookie" not in response.headers
    assert count(db_engine, User) == 0
    assert count(db_engine, IdempotencyRecord) == 0  # key released
    retry = api.post("/api/t/register", json={}, headers=headers)
    assert retry.status_code == 201 and count(db_engine, User) == 1


def registration_headers() -> dict:
    issued = create_registration_session(f"sub-{uuid.uuid4()}", "r@example.com")
    return {
        "Cookie": f"{REGISTRATION_COOKIE_NAME}={issued.token}", "Origin": ORIGIN,
        "X-CSRF-Token": issued.csrf_token, "Idempotency-Key": str(uuid.uuid4()),
    }


def test_bare_cookie_setter_cannot_outrun_the_request_commit(api, db_engine, monkeypatch):
    """A commit failing at the end of the request must not leave a 2xx or a Set-Cookie.

    Uses the *bare* `set_session_cookie` (cookie set before the commit) on purpose; that is
    safe because the handler commits before the response and `SessionDep` closes the session first.
    """
    user_id = new_user(db_engine)
    restore = break_commits(monkeypatch)
    response = api.post(f"/api/t/login-bare/{user_id}")
    restore()
    assert response.status_code >= 500, response.status_code
    assert "set-cookie" not in response.headers
    assert count(db_engine, AuthSession) == 0
