"""Idempotency subject = Google sub: registration session -> member session continuity,
and commit-before-cookie behaviour of the session helpers (issue #104)."""

import threading
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import idempotency
from app.auth import (
    REGISTRATION_COOKIE_NAME,
    SESSION_COOKIE_NAME,
    CurrentMemberOrRegistration,
    DbSession,
    MemberPrincipal,
    consume_registration_session,
    create_registration_session,
    create_session,
)
from app.csrf import CsrfMemberOrRegistration
from app.db.models import IdempotencyRecord, User
from app.errors import ApiError, ErrorCode, install_error_handlers
from app.idempotency import IdempotencyKey, IdempotentResult, run_idempotent, subject_id_for
from tests.factories import make_user

ORIGIN = "https://app.example.com"
REGISTER = "/api/t/register"


class RegisterIn(BaseModel):
    name: str


class Hooks:
    def __init__(self):
        self.calls = 0
        self.deny_replay = False
        self.lock = threading.Lock()


hooks = Hooks()


def build_app() -> FastAPI:
    app = FastAPI()
    install_error_handlers(app)

    def register_on(path: str):
        @app.post(path)
        def register(body: RegisterIn, caller: CsrfMemberOrRegistration, db: DbSession,
                     key: IdempotencyKey):
            """Stand-in for the #105 registration completion endpoint."""
            issued_box = {}

            def work() -> IdempotentResult:
                if isinstance(caller, MemberPrincipal):
                    raise ApiError(409, ErrorCode.ALREADY_REGISTERED)
                with hooks.lock:
                    hooks.calls += 1
                user = make_user(db, "WORKER", name=body.name, google_sub=caller.google_sub)
                if not consume_registration_session(caller.registration_id, db=db):
                    raise ApiError(409, ErrorCode.STATE_CONFLICT)
                issued_box["issued"] = create_session(user.id, db=db)
                return IdempotentResult(201, {"userId": user.id})

            def revalidate() -> None:
                if hooks.deny_replay:
                    raise ApiError(403, ErrorCode.FORBIDDEN)

            response = run_idempotent(
                db=db, principal=caller, key=key, method="POST", path=path, body=body,
                handler=work, revalidate=revalidate,
            )
            if "issued" in issued_box:  # run_idempotent already committed
                from app.auth import set_session_cookie
                set_session_cookie(response, issued_box["issued"])
            return response

    register_on(REGISTER)
    register_on("/api/t/register-b")

    @app.get("/api/t/who")
    def who(caller: CurrentMemberOrRegistration):
        return {"kind": type(caller).__name__}

    return app


@pytest.fixture(autouse=True)
def environment(monkeypatch):
    global hooks
    hooks = Hooks()
    monkeypatch.setenv("ALLOWED_ORIGINS", ORIGIN)
    monkeypatch.setattr(idempotency, "WAIT_TIMEOUT_SECONDS", 3.0)
    monkeypatch.setattr(idempotency, "POLL_INTERVAL_SECONDS", 0.02)


@pytest.fixture
def api(db_engine):
    return TestClient(build_app(), raise_server_exceptions=False)


def new_sub() -> str:
    return f"google-{uuid.uuid4()}"


def registration_headers(sub: str, key: str | None) -> dict:
    issued = create_registration_session(sub, f"{sub}@example.com")
    headers = {
        "Cookie": f"{REGISTRATION_COOKIE_NAME}={issued.token}", "Origin": ORIGIN,
        "X-CSRF-Token": issued.csrf_token,
    }
    if key:
        headers["Idempotency-Key"] = key
    return headers


def member_headers(user_id: str, key: str | None) -> dict:
    issued = create_session(user_id)
    headers = {
        "Cookie": f"{SESSION_COOKIE_NAME}={issued.token}", "Origin": ORIGIN,
        "X-CSRF-Token": issued.csrf_token,
    }
    if key:
        headers["Idempotency-Key"] = key
    return headers


def count_users(engine) -> int:
    with Session(engine) as session:
        return session.scalar(select(func.count()).select_from(User))


def register(api, sub, key, name="가입자", path=REGISTER):
    return api.post(path, json={"name": name}, headers=registration_headers(sub, key))


def retry_as_member(api, user_id, key, name="가입자", path=REGISTER):
    return api.post(path, json={"name": name}, headers=member_headers(user_id, key))


# --- subject derivation ----------------------------------------------------------------------

def test_subject_is_a_stable_hash_of_the_google_sub(db_engine):
    sub = "x" * 255  # the longest sub users.google_sub allows
    registration = create_registration_session(sub, "a@example.com")
    assert registration.token
    member = MemberPrincipal("u", sub, "WORKER", "s", "c", registration.expires_at)
    from app.auth import RegistrationPrincipal
    pre = RegistrationPrincipal("r", sub, "a@example.com", True, "c", registration.expires_at)
    assert subject_id_for(member) == subject_id_for(pre)
    assert len(subject_id_for(member)) == 64
    assert sub not in subject_id_for(member)
    other = MemberPrincipal("u", sub + "y", "WORKER", "s", "c", registration.expires_at)
    assert subject_id_for(other) != subject_id_for(member)


@pytest.mark.parametrize("sub", ["", None])
def test_subject_requires_a_google_sub(sub):
    principal = MemberPrincipal("u", sub, "WORKER", "s", "c", None)
    with pytest.raises(ValueError):
        subject_id_for(principal)


def test_dependency_prefers_member_then_registration_else_401(api, db_engine):
    sub = new_sub()
    assert api.get("/api/t/who").status_code == 401
    cookie = registration_headers(sub, None)["Cookie"]
    assert api.get("/api/t/who", headers={"Cookie": cookie}).json() == {"kind": "RegistrationPrincipal"}
    with Session(db_engine) as session:
        user = make_user(session, "WORKER")
        session.commit()
        user_id = user.id
    cookie = member_headers(user_id, None)["Cookie"]
    assert api.get("/api/t/who", headers={"Cookie": cookie}).json() == {"kind": "MemberPrincipal"}


# --- registration session -> member session --------------------------------------------------

def test_member_session_replays_the_registration_response(api, db_engine):
    sub, key = new_sub(), str(uuid.uuid4())
    first = register(api, sub, key)
    assert first.status_code == 201
    assert SESSION_COOKIE_NAME in first.headers["set-cookie"]
    user_id = first.json()["userId"]

    again = retry_as_member(api, user_id, key)
    assert again.status_code == 201
    assert again.json() == first.json()
    assert again.headers["Idempotent-Replayed"] == "true"
    assert "set-cookie" not in again.headers  # a replay never issues another session
    assert hooks.calls == 1 and count_users(db_engine) == 1
    with Session(db_engine) as session:
        assert session.scalar(select(func.count()).select_from(IdempotencyRecord)) == 1


def test_member_retry_with_another_body_is_a_key_conflict(api):
    sub, key = new_sub(), str(uuid.uuid4())
    user_id = register(api, sub, key).json()["userId"]
    response = retry_as_member(api, user_id, key, name="다른 이름")
    assert response.status_code == 409
    assert response.json()["code"] == "IDEMPOTENCY_KEY_REUSED"


def test_member_with_a_new_key_is_already_registered_and_leaves_no_record(api, db_engine):
    sub = new_sub()
    user_id = register(api, sub, str(uuid.uuid4())).json()["userId"]
    response = retry_as_member(api, user_id, str(uuid.uuid4()))
    assert response.status_code == 409
    assert response.json()["code"] == "ALREADY_REGISTERED"
    with Session(db_engine) as session:  # the failed attempt released its key
        assert session.scalar(select(func.count()).select_from(IdempotencyRecord)) == 1
    assert count_users(db_engine) == 1


def test_same_key_on_another_endpoint_conflicts_before_and_after_registration(api):
    sub, key = new_sub(), str(uuid.uuid4())
    user_id = register(api, sub, key).json()["userId"]
    other = retry_as_member(api, user_id, key, path="/api/t/register-b")
    assert other.status_code == 409 and other.json()["code"] == "IDEMPOTENCY_KEY_REUSED"
    # a second registration session of the same Google identity also cannot reuse it elsewhere
    again = register(api, sub, key, path="/api/t/register-b")
    assert again.status_code == 409 and again.json()["code"] == "IDEMPOTENCY_KEY_REUSED"


def test_different_google_subs_with_the_same_key_are_independent(api, db_engine):
    key = str(uuid.uuid4())
    a, b = register(api, new_sub(), key, name="a"), register(api, new_sub(), key, name="b")
    assert a.status_code == b.status_code == 201
    assert a.json()["userId"] != b.json()["userId"]
    # one person cannot replay another's registration with the same key
    other = retry_as_member(api, b.json()["userId"], key, name="a")
    assert other.status_code == 409
    assert hooks.calls == 2 and count_users(db_engine) == 2


def test_registration_session_replay_before_it_is_consumed_never_happens_twice(api, db_engine):
    sub, key = new_sub(), str(uuid.uuid4())
    assert register(api, sub, key).status_code == 201
    # A second registration session of the same person (re-login) replays the first result.
    again = register(api, sub, key)
    assert again.status_code == 201 and again.headers["Idempotent-Replayed"] == "true"
    assert count_users(db_engine) == 1 and hooks.calls == 1


def test_replay_revalidation_runs_for_both_session_kinds(api):
    sub, key = new_sub(), str(uuid.uuid4())
    user_id = register(api, sub, key).json()["userId"]
    hooks.deny_replay = True
    assert retry_as_member(api, user_id, key).status_code == 403
    assert register(api, sub, key).status_code == 403
    hooks.deny_replay = False
    assert retry_as_member(api, user_id, key).status_code == 201


def test_suspended_member_cannot_replay(api, db_engine):
    sub, key = new_sub(), str(uuid.uuid4())
    user_id = register(api, sub, key).json()["userId"]
    with Session(db_engine) as session:
        session.get(User, user_id).status = "SUSPENDED"
        session.commit()
    response = retry_as_member(api, user_id, key)
    assert response.status_code == 403 and response.json()["code"] == "ACCOUNT_SUSPENDED"


def test_missing_key_and_missing_session_are_rejected(api):
    assert register(api, new_sub(), None).status_code == 422
    response = api.post(REGISTER, json={"name": "x"}, headers={
        "Origin": ORIGIN, "Idempotency-Key": str(uuid.uuid4()),
    })
    assert response.status_code == 401


def test_concurrent_registrations_create_exactly_one_account(db_engine):
    sub, key = new_sub(), str(uuid.uuid4())
    client = TestClient(build_app(), raise_server_exceptions=False)
    headers = [registration_headers(sub, key) for _ in range(6)]
    results = [None] * 6
    barrier = threading.Barrier(6)

    def worker(i):
        barrier.wait()
        results[i] = client.post(REGISTER, json={"name": "동시"}, headers=headers[i])

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    statuses = [r.status_code for r in results]
    assert statuses.count(201) >= 1 and set(statuses) <= {201, 409}, statuses
    assert count_users(db_engine) == 1 and hooks.calls == 1
    # the lost responses are recoverable with the member session
    with Session(db_engine) as session:
        user_id = session.scalars(select(User)).one().id
    assert retry_as_member(client, user_id, key, name="동시").status_code == 201
