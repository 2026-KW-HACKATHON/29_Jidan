from datetime import timedelta
from typing import Annotated

import pytest
from fastapi import Depends, FastAPI, Response
from fastapi.testclient import TestClient
from sqlalchemy import inspect, select, text
from sqlalchemy.orm import Session

from app import auth
from app.auth import (
    REGISTRATION_COOKIE_NAME,
    SESSION_COOKIE_NAME,
    CurrentMember,
    CurrentOwner,
    CurrentRegistration,
    CurrentWorker,
    IssuedSession,
    MemberPrincipal,
    clear_registration_cookie,
    clear_session_cookie,
    consume_registration_session,
    create_registration_session,
    create_session,
    hash_token,
    optional_member,
    revoke_registration_session,
    revoke_session,
    revoke_user_sessions,
    set_registration_cookie,
    set_session_cookie,
)
from app.db import session_scope, utcnow
from app.db.models import AuthSession, RegistrationSession, User
from app.errors import install_error_handlers
from app.middleware import install_middleware
from tests.factories import make_user


def build_app() -> FastAPI:
    app = FastAPI()
    install_error_handlers(app)
    install_middleware(app)

    @app.get("/api/t/member")
    def member(principal: CurrentMember) -> dict:
        return {"userId": principal.user_id, "role": principal.role}

    @app.get("/api/t/optional")
    def optional(principal: Annotated[MemberPrincipal | None, Depends(optional_member)]) -> dict:
        return {"member": principal is not None}

    @app.get("/api/t/owner")
    def owner(principal: CurrentOwner) -> dict:
        return {"userId": principal.user_id}

    @app.get("/api/t/worker")
    def worker(principal: CurrentWorker) -> dict:
        return {"userId": principal.user_id}

    @app.get("/api/auth/t/registration")
    def registration(principal: CurrentRegistration) -> dict:
        return {"email": principal.google_email, "sub": principal.google_sub}

    @app.post("/api/t/issue/{user_id}")
    def issue(user_id: str, response: Response) -> dict:
        set_session_cookie(response, create_session(user_id))
        return {}

    return app


@pytest.fixture
def api(db_engine):
    return TestClient(build_app())


def add_user(engine, role="WORKER", **overrides) -> str:
    with Session(engine) as session:
        user_id = make_user(session, role, **overrides).id
        session.commit()
    return user_id


def cookie(name, token) -> dict:
    return {"Cookie": f"{name}={token}"}


def member_headers(token) -> dict:
    return cookie(SESSION_COOKIE_NAME, token)


def get_code(response):
    return response.json()["code"]


def change_session(engine, token, **values):
    with Session(engine) as session:
        row = session.execute(
            select(AuthSession).where(AuthSession.token_hash == hash_token(token))
        ).scalar_one()
        for key, value in values.items():
            setattr(row, key, value)
        session.commit()


def change_registration(engine, token, **values):
    with Session(engine) as session:
        row = session.execute(
            select(RegistrationSession).where(RegistrationSession.token_hash == hash_token(token))
        ).scalar_one()
        for key, value in values.items():
            setattr(row, key, value)
        session.commit()


def load_session(engine, token) -> AuthSession:
    with Session(engine) as session:
        return session.execute(
            select(AuthSession).where(AuthSession.token_hash == hash_token(token))
        ).scalar_one()


# --- member sessions -------------------------------------------------------------------------

def test_valid_member_session_is_resolved(api, db_engine):
    user_id = add_user(db_engine, "OWNER")
    issued = create_session(user_id)
    response = api.get("/api/t/member", headers=member_headers(issued.token))
    assert response.status_code == 200
    assert response.json() == {"userId": user_id, "role": "OWNER"}


def test_missing_cookie_is_session_expired(api):
    response = api.get("/api/t/member")
    assert response.status_code == 401
    assert get_code(response) == "SESSION_EXPIRED"


@pytest.mark.parametrize("value", ["", "garbage", "a" * 5000, "x y", "%00", "' OR '1'='1"])
def test_unknown_or_malformed_tokens_are_session_expired(api, value):
    response = api.get("/api/t/member", headers=member_headers(value))
    assert response.status_code == 401
    assert get_code(response) == "SESSION_EXPIRED"


def test_only_the_token_hash_is_stored(db_engine):
    user_id = add_user(db_engine)
    issued = create_session(user_id)
    with db_engine.connect() as connection:
        row = connection.execute(text("SELECT * FROM auth_sessions")).mappings().one()
    assert row["token_hash"] == hash_token(issued.token)
    assert issued.token not in {str(value) for value in row.values()}
    assert len(row["token_hash"]) == 64


def test_tokens_are_unique_and_unguessable_in_length(db_engine):
    user_id = add_user(db_engine)
    tokens = {create_session(user_id).token for _ in range(20)}
    assert len(tokens) == 20
    assert all(len(token) >= 43 for token in tokens)


def test_absolute_expiry_ends_the_session(api, db_engine):
    token = create_session(add_user(db_engine)).token
    change_session(db_engine, token, expires_at=utcnow() - timedelta(seconds=1))
    response = api.get("/api/t/member", headers=member_headers(token))
    assert response.status_code == 401 and get_code(response) == "SESSION_EXPIRED"


def test_idle_expiry_ends_the_session(api, db_engine):
    token = create_session(add_user(db_engine)).token
    change_session(db_engine, token, last_seen_at=utcnow() - timedelta(hours=24, seconds=1))
    assert api.get("/api/t/member", headers=member_headers(token)).status_code == 401


def test_session_just_inside_the_idle_window_still_works(api, db_engine):
    token = create_session(add_user(db_engine)).token
    change_session(db_engine, token, last_seen_at=utcnow() - timedelta(hours=23, minutes=59))
    assert api.get("/api/t/member", headers=member_headers(token)).status_code == 200


def test_activity_slides_the_idle_window_but_not_the_absolute_limit(api, db_engine):
    token = create_session(add_user(db_engine)).token
    old = utcnow() - timedelta(hours=20)
    change_session(db_engine, token, last_seen_at=old)
    before = load_session(db_engine, token)
    assert api.get("/api/t/member", headers=member_headers(token)).status_code == 200
    after = load_session(db_engine, token)
    assert after.last_seen_at > old
    assert after.expires_at == before.expires_at


def test_recent_activity_does_not_write_on_every_request(api, db_engine):
    token = create_session(add_user(db_engine)).token
    before = load_session(db_engine, token).last_seen_at
    api.get("/api/t/member", headers=member_headers(token))
    assert load_session(db_engine, token).last_seen_at == before


def test_member_session_lifetime_is_seven_days(db_engine):
    issued = create_session(add_user(db_engine))
    remaining = issued.expires_at - utcnow()
    assert timedelta(days=6, hours=23) < remaining <= timedelta(days=7)
    assert 6 * 86400 < issued.max_age <= 7 * 86400


def test_revoked_session_is_rejected(api, db_engine):
    token = create_session(add_user(db_engine)).token
    assert revoke_session(token) is True
    assert revoke_session(token) is False
    assert revoke_session("unknown-token") is False
    assert api.get("/api/t/member", headers=member_headers(token)).status_code == 401


def test_revoke_user_sessions_ends_every_session_of_that_user_only(api, db_engine):
    user_id, other_id = add_user(db_engine), add_user(db_engine)
    first, second = create_session(user_id).token, create_session(user_id).token
    other = create_session(other_id).token
    assert revoke_user_sessions(user_id) == 2
    assert api.get("/api/t/member", headers=member_headers(first)).status_code == 401
    assert api.get("/api/t/member", headers=member_headers(second)).status_code == 401
    assert api.get("/api/t/member", headers=member_headers(other)).status_code == 200


def test_suspended_account_is_forbidden_and_its_session_is_revoked(api, db_engine):
    user_id = add_user(db_engine, status="SUSPENDED")
    token = create_session(user_id).token
    response = api.get("/api/t/member", headers=member_headers(token))
    assert response.status_code == 403 and get_code(response) == "ACCOUNT_SUSPENDED"
    assert load_session(db_engine, token).revoked_at is not None  # persisted despite the 403
    with Session(db_engine) as session:  # reinstating the account does not revive the session
        session.get(User, user_id).status = "ACTIVE"
        session.commit()
    assert get_code(api.get("/api/t/member", headers=member_headers(token))) == "SESSION_EXPIRED"


def test_suspension_after_login_takes_effect_on_the_next_request(api, db_engine):
    user_id = add_user(db_engine)
    token = create_session(user_id).token
    assert api.get("/api/t/member", headers=member_headers(token)).status_code == 200
    with Session(db_engine) as session:
        session.get(User, user_id).status = "SUSPENDED"
        session.commit()
    response = api.get("/api/t/member", headers=member_headers(token))
    assert get_code(response) == "ACCOUNT_SUSPENDED"


def test_role_change_applies_immediately_because_roles_are_not_cached(api, db_engine):
    user_id = add_user(db_engine, "WORKER")
    token = create_session(user_id).token
    assert api.get("/api/t/worker", headers=member_headers(token)).status_code == 200
    with Session(db_engine) as session:
        session.get(User, user_id).role = "OWNER"
        session.commit()
    assert api.get("/api/t/worker", headers=member_headers(token)).status_code == 403


def test_role_mismatch_is_forbidden(api, db_engine):
    worker = create_session(add_user(db_engine, "WORKER")).token
    owner = create_session(add_user(db_engine, "OWNER")).token
    response = api.get("/api/t/owner", headers=member_headers(worker))
    assert response.status_code == 403 and get_code(response) == "FORBIDDEN"
    response = api.get("/api/t/worker", headers=member_headers(owner))
    assert response.status_code == 403 and get_code(response) == "FORBIDDEN"
    assert api.get("/api/t/owner", headers=member_headers(owner)).status_code == 200
    assert api.get("/api/t/worker", headers=member_headers(worker)).status_code == 200


def test_role_dependencies_still_require_a_session(api):
    assert get_code(api.get("/api/t/owner")) == "SESSION_EXPIRED"
    assert get_code(api.get("/api/t/worker")) == "SESSION_EXPIRED"


def test_optional_member_never_fails_for_anonymous_callers(api, db_engine):
    assert api.get("/api/t/optional").json() == {"member": False}
    token = create_session(add_user(db_engine)).token
    assert api.get("/api/t/optional", headers=member_headers(token)).json() == {"member": True}


def test_create_session_with_the_callers_transaction_commits_with_it(db_engine):
    user_id = add_user(db_engine)
    with Session(db_engine) as session:
        issued = create_session(user_id, db=session)
        session.rollback()
    with Session(db_engine) as session:
        assert session.scalars(select(AuthSession)).all() == []
    assert issued.token


def test_login_style_endpoint_sets_a_working_cookie(api, db_engine):
    user_id = add_user(db_engine)
    response = api.post(f"/api/t/issue/{user_id}")
    set_cookie = response.headers["set-cookie"]
    assert set_cookie.startswith(f"{SESSION_COOKIE_NAME}=")
    token = set_cookie.split(";")[0].split("=", 1)[1]
    assert api.get("/api/t/member", headers=member_headers(token)).status_code == 200


# --- registration sessions -------------------------------------------------------------------

def registration_headers(token) -> dict:
    return cookie(REGISTRATION_COOKIE_NAME, token)


def test_registration_session_is_resolved_with_its_google_identity(api):
    issued = create_registration_session("google-sub-1", "new@example.com")
    response = api.get("/api/auth/t/registration", headers=registration_headers(issued.token))
    assert response.status_code == 200
    assert response.json() == {"email": "new@example.com", "sub": "google-sub-1"}


def test_registration_session_lasts_ten_minutes(db_engine):
    issued = create_registration_session("s", "e@example.com")
    assert timedelta(minutes=9, seconds=55) < issued.expires_at - utcnow() <= timedelta(minutes=10)
    assert 595 <= issued.max_age <= 600


def test_registration_token_is_stored_only_as_a_hash(db_engine):
    issued = create_registration_session("s", "e@example.com")
    with db_engine.connect() as connection:
        row = connection.execute(text("SELECT * FROM registration_sessions")).mappings().one()
    assert row["token_hash"] == hash_token(issued.token)
    assert issued.token not in {str(value) for value in row.values()}


def test_registration_only_caller_gets_registration_required_on_member_apis(api):
    issued = create_registration_session("s", "e@example.com")
    response = api.get("/api/t/member", headers=registration_headers(issued.token))
    assert response.status_code == 401
    assert get_code(response) == "REGISTRATION_REQUIRED"
    # also for role-restricted endpoints
    response = api.get("/api/t/owner", headers=registration_headers(issued.token))
    assert get_code(response) == "REGISTRATION_REQUIRED"


def test_registration_cookie_value_in_the_member_cookie_does_not_authenticate(api):
    issued = create_registration_session("s", "e@example.com")
    response = api.get("/api/t/member", headers=member_headers(issued.token))
    assert response.status_code == 401 and get_code(response) == "SESSION_EXPIRED"


def test_member_cookie_does_not_open_the_registration_endpoint(api, db_engine):
    token = create_session(add_user(db_engine)).token
    for headers in (member_headers(token), registration_headers(token)):
        response = api.get("/api/auth/t/registration", headers=headers)
        assert response.status_code == 401 and get_code(response) == "SESSION_EXPIRED"


def test_expired_registration_session_is_session_expired(api, db_engine):
    token = create_registration_session("s", "e@example.com").token
    change_registration(db_engine, token, expires_at=utcnow() - timedelta(seconds=1))
    for path in ("/api/auth/t/registration", "/api/t/member"):
        response = api.get(path, headers=registration_headers(token))
        assert response.status_code == 401 and get_code(response) == "SESSION_EXPIRED"


def test_consumed_or_revoked_registration_session_is_rejected(api):
    consumed = create_registration_session("s1", "a@example.com").token
    revoked = create_registration_session("s2", "b@example.com").token
    assert revoke_registration_session(revoked) is True
    assert revoke_registration_session(revoked) is False
    assert revoke_registration_session("nope") is False
    with session_scope() as s:
        row = s.execute(
            select(RegistrationSession).where(RegistrationSession.token_hash == hash_token(consumed))
        ).scalar_one()
        assert consume_registration_session(row.id, db=s) is True
        assert consume_registration_session(row.id, db=s) is False
    for token in (consumed, revoked):
        response = api.get("/api/auth/t/registration", headers=registration_headers(token))
        assert response.status_code == 401


def test_expired_registration_session_cannot_be_consumed(db_engine):
    token = create_registration_session("s", "e@example.com").token
    change_registration(db_engine, token, expires_at=utcnow() - timedelta(seconds=1))
    with session_scope() as session:
        row = session.execute(
            select(RegistrationSession).where(RegistrationSession.token_hash == hash_token(token))
        ).scalar_one()
        assert consume_registration_session(row.id, db=session) is False


def test_member_session_wins_when_both_cookies_are_present(api, db_engine):
    user_id = add_user(db_engine)
    member = create_session(user_id).token
    registration = create_registration_session("s", "e@example.com").token
    headers = {"Cookie": f"{SESSION_COOKIE_NAME}={member}; {REGISTRATION_COOKIE_NAME}={registration}"}
    assert api.get("/api/t/member", headers=headers).json()["userId"] == user_id


def test_expired_member_session_with_live_registration_session_asks_for_registration(api, db_engine):
    member = create_session(add_user(db_engine)).token
    change_session(db_engine, member, expires_at=utcnow() - timedelta(seconds=1))
    registration = create_registration_session("s", "e@example.com").token
    headers = {"Cookie": f"{SESSION_COOKIE_NAME}={member}; {REGISTRATION_COOKIE_NAME}={registration}"}
    assert get_code(api.get("/api/t/member", headers=headers)) == "REGISTRATION_REQUIRED"


# --- cookie attributes -----------------------------------------------------------------------

def set_cookie_of(function, issued=None) -> str:
    response = Response()
    function(response) if issued is None else function(response, issued)
    return response.headers["set-cookie"]


def attributes(header: str) -> dict:
    parts = [part.strip() for part in header.split(";")]
    result = {"value": parts[0]}
    for part in parts[1:]:
        key, _, value = part.partition("=")
        result[key.lower()] = value
    return result


@pytest.fixture
def local_env(monkeypatch):
    monkeypatch.delenv("COOKIE_SECURE", raising=False)
    monkeypatch.setenv("APP_ENV", "local")


def test_member_cookie_attributes(local_env, monkeypatch):
    issued = IssuedSession("tok", utcnow() + timedelta(days=7))
    attrs = attributes(set_cookie_of(set_session_cookie, issued))
    assert attrs["value"] == f"{SESSION_COOKIE_NAME}=tok"
    assert "httponly" in attrs
    assert attrs["samesite"] == "lax"
    assert attrs["path"] == "/"
    assert 7 * 86400 - 5 <= int(attrs["max-age"]) <= 7 * 86400
    assert "domain" not in attrs
    assert "secure" not in attrs  # local HTTP only


def test_registration_cookie_attributes(local_env):
    issued = IssuedSession("tok", utcnow() + timedelta(minutes=10))
    attrs = attributes(set_cookie_of(set_registration_cookie, issued))
    assert attrs["value"] == f"{REGISTRATION_COOKIE_NAME}=tok"
    assert "httponly" in attrs and attrs["samesite"] == "lax"
    assert attrs["path"] == "/api/auth"
    assert 595 <= int(attrs["max-age"]) <= 600
    assert "domain" not in attrs


def secure_cookie_for(monkeypatch, environment, override):
    for name, value in (("APP_ENV", environment), ("COOKIE_SECURE", override)):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    issued = IssuedSession("tok", utcnow() + timedelta(days=1))
    return {
        "member": "secure" in attributes(set_cookie_of(set_session_cookie, issued)),
        "registration": "secure" in attributes(set_cookie_of(set_registration_cookie, issued)),
    }


@pytest.mark.parametrize("environment", ["production", "dev", "unknown"])
def test_cookies_are_secure_outside_local(monkeypatch, environment):
    assert secure_cookie_for(monkeypatch, environment, None) == {"member": True, "registration": True}


@pytest.mark.parametrize("override", ["true", "TRUE", " true "])
@pytest.mark.parametrize("environment", ["production", "dev", "local", "unknown"])
def test_cookie_secure_true_is_honored_everywhere(monkeypatch, environment, override):
    assert secure_cookie_for(monkeypatch, environment, override)["member"] is True


@pytest.mark.parametrize("override", ["false", "FALSE", " false "])
def test_production_never_accepts_insecure_cookies(monkeypatch, override):
    with pytest.raises(auth.ConfigurationError):
        secure_cookie_for(monkeypatch, "production", override)


def test_unknown_environment_is_treated_like_production(monkeypatch):
    with pytest.raises(auth.ConfigurationError):
        secure_cookie_for(monkeypatch, "prod", "false")


def test_dev_may_opt_out_of_secure_for_its_http_server(monkeypatch):
    assert secure_cookie_for(monkeypatch, "dev", "false") == {"member": False, "registration": False}


def test_local_is_insecure_by_default_and_when_false(monkeypatch):
    assert secure_cookie_for(monkeypatch, "local", None)["member"] is False
    assert secure_cookie_for(monkeypatch, "local", "false")["member"] is False
    assert secure_cookie_for(monkeypatch, None, None)["member"] is False  # APP_ENV unset = local


@pytest.mark.parametrize("environment", ["production", "dev", "local"])
@pytest.mark.parametrize("override", ["maybe", "ture", "1", "0", "yes", "no", "false true"])
def test_invalid_cookie_secure_value_is_rejected_not_ignored(monkeypatch, environment, override):
    with pytest.raises(auth.ConfigurationError):
        secure_cookie_for(monkeypatch, environment, override)


def test_blank_cookie_secure_counts_as_unset(monkeypatch):
    assert secure_cookie_for(monkeypatch, "production", "  ")["member"] is True


def test_validation_fails_application_start(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("COOKIE_SECURE", "false")
    with pytest.raises(auth.ConfigurationError):
        auth.validate_cookie_settings()
    monkeypatch.setenv("COOKIE_SECURE", "true")
    auth.validate_cookie_settings()


def test_importing_the_app_fails_with_an_insecure_production_config(monkeypatch):
    import importlib
    import sys

    import app.main

    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("COOKIE_SECURE", "false")
    try:
        with pytest.raises(auth.ConfigurationError):
            importlib.reload(app.main)
    finally:
        monkeypatch.undo()
        importlib.reload(sys.modules["app.main"])


def test_clearing_cookies_expires_them_with_the_same_scope(local_env):
    member = attributes(set_cookie_of(clear_session_cookie))
    registration = attributes(set_cookie_of(clear_registration_cookie))
    assert member["max-age"] == "0" and member["path"] == "/"
    assert registration["max-age"] == "0" and registration["path"] == "/api/auth"


def test_issued_session_does_not_leak_its_token_in_repr():
    assert "secret-token" not in repr(IssuedSession("secret-token", utcnow()))


def test_session_tables_exist_with_unique_hashes(db_engine):
    inspector = inspect(db_engine)
    for table in ("auth_sessions", "registration_sessions"):
        uniques = [tuple(u["column_names"]) for u in inspector.get_unique_constraints(table)]
        indexes = [tuple(i["column_names"]) for i in inspector.get_indexes(table) if i["unique"]]
        assert ("token_hash",) in uniques + indexes
