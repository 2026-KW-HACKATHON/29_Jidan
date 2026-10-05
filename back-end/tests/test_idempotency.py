import sqlite3
import threading
import time
import uuid
from datetime import timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app import idempotency
from app.auth import SESSION_COOKIE_NAME, DbSession, MemberPrincipal, create_session
from app.csrf import CsrfOwner
from app.db import session_scope, utcnow
from app.db.models import IdempotencyRecord, User
from app.errors import ApiError, ErrorCode, install_error_handlers
from app.idempotency import (
    IdempotencyKey,
    IdempotentResult,
    body_hash,
    canonical_body,
    purge_expired,
    run_idempotent,
    subject_id_for,
)
from tests.factories import make_user

ORIGIN = "https://app.example.com"


class ThingIn(BaseModel):
    name: str
    tags: list[str] = []


class Hooks:
    """Lets a test observe and steer the endpoint under test."""

    def __init__(self):
        self.calls = 0
        self.fail_with: Exception | None = None
        self.deny_replay = False
        self.pause = 0.0
        self.lock = threading.Lock()


hooks = Hooks()


def build_app() -> FastAPI:
    app = FastAPI()
    install_error_handlers(app)

    @app.post("/api/t/things")
    def create(body: ThingIn, owner: CsrfOwner, db: DbSession, key: IdempotencyKey):
        def work() -> IdempotentResult:
            with hooks.lock:
                hooks.calls += 1
            if hooks.pause:
                time.sleep(hooks.pause)
            thing = make_user(db, "WORKER", name=body.name)
            if hooks.fail_with is not None:
                raise hooks.fail_with
            return IdempotentResult(201, {"id": thing.id, "name": body.name})

        def revalidate() -> None:
            if hooks.deny_replay:
                raise ApiError(403, ErrorCode.FORBIDDEN)

        return run_idempotent(
            db=db, principal=owner, key=key, method="POST",
            path="/api/t/things", body=body, handler=work, revalidate=revalidate,
        )

    @app.post("/api/t/savepoint")
    def savepoint(body: ThingIn, owner: CsrfOwner, db: DbSession, key: IdempotencyKey):
        def work() -> IdempotentResult:
            with db.begin_nested():  # released savepoint: its write must reach the final commit
                kept = make_user(db, "WORKER", name=body.name)
            try:
                with db.begin_nested():  # rolled back: duplicate google_sub
                    make_user(db, "WORKER", google_sub=kept.google_sub)
            except IntegrityError:
                pass
            return IdempotentResult(201, {"id": kept.id})

        return run_idempotent(
            db=db, principal=owner, key=key, method="POST", path="/api/t/savepoint",
            body=body, handler=work,
        )

    @app.post("/api/t/other")
    def other(body: ThingIn, owner: CsrfOwner, db: DbSession, key: IdempotencyKey):
        return run_idempotent(
            db=db, principal=owner, key=key, method="POST", path="/api/t/other",
            body=body, handler=lambda: IdempotentResult(201, {"other": True}),
        )

    @app.post("/api/t/empty")
    def empty(body: ThingIn, owner: CsrfOwner, db: DbSession, key: IdempotencyKey):
        return run_idempotent(
            db=db, principal=owner, key=key, method="POST", path="/api/t/empty",
            body=body, handler=lambda: IdempotentResult(204),
        )

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
    return TestClient(build_app())


def add_owner(engine) -> tuple[str, str]:
    with Session(engine) as session:
        user = make_user(session, "OWNER")
        session.commit()
        return user.id, user.google_sub


class Caller:
    def __init__(self, engine):
        self.user_id, self.google_sub = add_owner(engine)
        self.issued = create_session(self.user_id)

    @property
    def principal(self) -> MemberPrincipal:
        return MemberPrincipal(
            self.user_id, self.google_sub, "OWNER", "session", self.issued.csrf_token,
            self.issued.expires_at,
        )

    @property
    def subject_id(self) -> str:
        return subject_id_for(self.principal)

    def headers(self, key=None) -> dict:
        headers = {
            "Cookie": f"{SESSION_COOKIE_NAME}={self.issued.token}", "Origin": ORIGIN,
            "X-CSRF-Token": self.issued.csrf_token,
        }
        if key is not None:
            headers["Idempotency-Key"] = key
        return headers


@pytest.fixture
def caller(db_engine):
    return Caller(db_engine)


def new_key() -> str:
    return str(uuid.uuid4())


def calls_made() -> int:
    return hooks.calls


def count_users(engine) -> int:
    with Session(engine) as session:
        return session.scalar(select(func.count()).select_from(User))


def records(engine) -> list[IdempotencyRecord]:
    with Session(engine) as session:
        return list(session.scalars(select(IdempotencyRecord)))


def post(api, caller, key, body=None, path="/api/t/things"):
    return api.post(path, json=body if body is not None else {"name": "a"}, headers=caller.headers(key))


# --- header validation -----------------------------------------------------------------------

def test_missing_key_is_a_validation_error(api, caller):
    response = post(api, caller, None)
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert body["fieldErrors"][0]["field"] == "Idempotency-Key"
    assert body["fieldErrors"][0]["code"] == "REQUIRED"


@pytest.mark.parametrize("value", [
    "", " ", "abc", "not-a-uuid", "12345678123456781234567812345678",
    "{12345678-1234-5678-1234-567812345678}", "urn:uuid:12345678-1234-5678-1234-567812345678",
    "12345678-1234-5678-1234-56781234567", "12345678-1234-5678-1234-5678123456789",
    "12345678-1234-5678-1234-56781234567g", "' OR 1=1 --", "1" * 300,
    " 12345678-1234-5678-1234-567812345678",
])
def test_malformed_keys_are_rejected(api, caller, db_engine, value):
    response = post(api, caller, value)
    assert response.status_code == 422
    assert response.json()["fieldErrors"][0]["field"] == "Idempotency-Key"
    assert count_users(db_engine) == 1  # only the owner exists; nothing was created


def test_uppercase_and_lowercase_keys_are_the_same_key(api, caller, db_engine):
    key = new_key()
    first = post(api, caller, key.upper())
    second = post(api, caller, key.lower())
    assert first.status_code == 201
    assert second.json() == first.json()
    assert second.headers["Idempotent-Replayed"] == "true"
    # Policy: the app lower-cases the key before it is stored or compared (the column is also
    # case-sensitive on MySQL, so nothing depends on the database folding case).
    (record,) = records(db_engine)
    assert record.idempotency_key == key.lower() and calls_made() == 1


def test_handler_savepoints_commit_with_the_response_and_never_trip_the_write_guard(
    api, caller, db_engine,
):
    key = new_key()
    first = post(api, caller, key, path="/api/t/savepoint")
    assert first.status_code == 201  # no UncommittedWriteError (500) from the released savepoint
    assert count_users(db_engine) == 2  # owner + the savepoint user; the undone insert is gone
    second = post(api, caller, key, path="/api/t/savepoint")
    assert second.json() == first.json() and second.headers["Idempotent-Replayed"] == "true"
    assert count_users(db_engine) == 2


# --- replay, conflict ------------------------------------------------------------------------

def test_first_request_runs_and_a_retry_replays_it(api, caller, db_engine):
    key = new_key()
    first = post(api, caller, key)
    assert first.status_code == 201
    assert "Idempotent-Replayed" not in first.headers
    second = post(api, caller, key)
    assert second.status_code == 201
    assert second.json() == first.json()
    assert second.headers["Idempotent-Replayed"] == "true"
    assert hooks.calls == 1
    assert count_users(db_engine) == 2  # the owner and one created user


def test_replay_works_repeatedly(api, caller):
    key = new_key()
    first = post(api, caller, key).json()
    assert all(post(api, caller, key).json() == first for _ in range(3))
    assert hooks.calls == 1


def test_json_key_order_and_whitespace_do_not_matter(api, caller):
    key = new_key()
    first = api.post(
        "/api/t/things", content='{"name": "a", "tags": ["x", "y"]}',
        headers={**caller.headers(key), "Content-Type": "application/json"},
    )
    second = api.post(
        "/api/t/things", content='{"tags":["x","y"],   "name":"a"}',
        headers={**caller.headers(key), "Content-Type": "application/json"},
    )
    assert second.headers["Idempotent-Replayed"] == "true"
    assert second.json() == first.json()


def test_defaults_are_part_of_the_normalized_body(api, caller):
    key = new_key()
    first = post(api, caller, key, {"name": "a"})
    second = post(api, caller, key, {"name": "a", "tags": []})
    assert second.json() == first.json()


def test_same_key_with_a_different_body_is_a_conflict(api, caller, db_engine):
    key = new_key()
    assert post(api, caller, key, {"name": "a"}).status_code == 201
    response = post(api, caller, key, {"name": "b"})
    assert response.status_code == 409
    assert response.json()["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert hooks.calls == 1
    assert count_users(db_engine) == 2


def test_list_order_changes_the_body(api, caller):
    key = new_key()
    post(api, caller, key, {"name": "a", "tags": ["x", "y"]})
    response = post(api, caller, key, {"name": "a", "tags": ["y", "x"]})
    assert response.status_code == 409


def test_same_key_on_another_endpoint_is_a_conflict(api, caller):
    key = new_key()
    assert post(api, caller, key, path="/api/t/things").status_code == 201
    response = post(api, caller, key, path="/api/t/other")
    assert response.status_code == 409 and response.json()["code"] == "IDEMPOTENCY_KEY_REUSED"


def test_keys_are_scoped_to_the_member(api, db_engine):
    first, second = Caller(db_engine), Caller(db_engine)
    key = new_key()
    a = post(api, first, key)
    b = post(api, second, key)
    assert a.status_code == b.status_code == 201
    assert a.json()["id"] != b.json()["id"]
    assert hooks.calls == 2


def test_unicode_bodies_are_normalized_consistently(api, caller):
    key = new_key()
    first = post(api, caller, key, {"name": "김지수 ☕"})
    second = post(api, caller, key, {"name": "김지수 ☕"})
    assert second.json() == first.json()


def test_empty_responses_are_replayed_without_a_body(api, caller):
    key = new_key()
    first = post(api, caller, key, path="/api/t/empty")
    second = post(api, caller, key, path="/api/t/empty")
    assert first.status_code == second.status_code == 204
    assert second.content == b"" and second.headers["Idempotent-Replayed"] == "true"


def test_stored_row_holds_hashes_not_the_request_body(api, caller, db_engine):
    post(api, caller, new_key(), {"name": "super-secret-name"})
    (record,) = records(db_engine)
    assert record.request_hash == body_hash({"name": "super-secret-name", "tags": []})
    assert len(record.request_hash) == 64 and record.state == "COMPLETED"
    assert "super-secret-name" not in f"{record.endpoint}{record.request_hash}"
    assert record.expires_at - record.created_at == timedelta(hours=24)


# --- failures --------------------------------------------------------------------------------

def test_failed_handler_rolls_back_and_frees_the_key(api, caller, db_engine):
    key = new_key()
    hooks.fail_with = ApiError(409, ErrorCode.STATE_CONFLICT)
    response = post(api, caller, key)
    assert response.status_code == 409
    assert count_users(db_engine) == 1  # business write rolled back
    assert records(db_engine) == []
    hooks.fail_with = None
    retry = post(api, caller, key)
    assert retry.status_code == 201 and "Idempotent-Replayed" not in retry.headers
    assert count_users(db_engine) == 2


def test_unexpected_exception_also_frees_the_key(db_engine):
    caller = Caller(db_engine)
    key = new_key()
    hooks.fail_with = RuntimeError("boom")
    client = TestClient(build_app(), raise_server_exceptions=False)
    assert post(client, caller, key).status_code == 500
    assert records(db_engine) == []
    hooks.fail_with = None
    assert post(client, caller, key).status_code == 201


def test_errors_are_not_replayed_as_results(api, caller):
    key = new_key()
    hooks.fail_with = ApiError(409, ErrorCode.STATE_CONFLICT)
    assert post(api, caller, key).status_code == 409
    hooks.fail_with = ApiError(403, ErrorCode.FORBIDDEN)
    assert post(api, caller, key).status_code == 403  # re-executed, not a stored 409


def test_validation_failures_do_not_consume_the_key(api, caller, db_engine):
    key = new_key()
    assert post(api, caller, key, {"tags": []}).status_code == 422
    assert records(db_engine) == []
    assert post(api, caller, key).status_code == 201


# --- permissions on replay -------------------------------------------------------------------

def test_replay_runs_the_revalidation_hook(api, caller):
    key = new_key()
    assert post(api, caller, key).status_code == 201
    hooks.deny_replay = True
    response = post(api, caller, key)
    assert response.status_code == 403 and response.json()["code"] == "FORBIDDEN"
    assert "Idempotent-Replayed" not in response.headers


def test_hook_is_not_used_for_the_first_execution(api, caller):
    hooks.deny_replay = True
    assert post(api, caller, new_key()).status_code == 201


def test_suspended_member_cannot_replay(api, caller, db_engine):
    key = new_key()
    assert post(api, caller, key).status_code == 201
    with Session(db_engine) as session:
        session.get(User, caller.user_id).status = "SUSPENDED"
        session.commit()
    response = post(api, caller, key)
    assert response.status_code == 403 and response.json()["code"] == "ACCOUNT_SUSPENDED"


def test_revoked_session_cannot_replay(api, caller):
    from app.auth import revoke_session

    key = new_key()
    assert post(api, caller, key).status_code == 201
    revoke_session(caller.issued.token)
    assert post(api, caller, key).status_code == 401


def test_csrf_is_still_enforced_on_a_replay(api, caller):
    key = new_key()
    post(api, caller, key)
    headers = caller.headers(key)
    headers["X-CSRF-Token"] = "wrong"
    response = api.post("/api/t/things", json={"name": "a"}, headers=headers)
    assert response.status_code == 403


# --- expiry and leases -----------------------------------------------------------------------

def set_record(engine, **values):
    with Session(engine) as session:
        record = session.scalars(select(IdempotencyRecord)).one()
        for name, value in values.items():
            setattr(record, name, value)
        session.commit()


def test_records_expire_after_24_hours(api, caller, db_engine):
    key = new_key()
    first = post(api, caller, key).json()
    set_record(db_engine, expires_at=utcnow() - timedelta(seconds=1))
    second = post(api, caller, key)
    assert second.status_code == 201 and "Idempotent-Replayed" not in second.headers
    assert second.json()["id"] != first["id"]
    assert hooks.calls == 2


def test_expired_key_can_be_reused_with_a_different_body(api, caller, db_engine):
    key = new_key()
    post(api, caller, key, {"name": "a"})
    set_record(db_engine, expires_at=utcnow() - timedelta(seconds=1))
    assert post(api, caller, key, {"name": "b"}).status_code == 201


def test_record_just_inside_24_hours_is_still_replayed(api, caller, db_engine):
    key = new_key()
    post(api, caller, key)
    set_record(db_engine, expires_at=utcnow() + timedelta(seconds=30))
    assert post(api, caller, key).headers["Idempotent-Replayed"] == "true"


def test_purge_removes_only_expired_records(api, caller, db_engine):
    post(api, caller, new_key())
    post(api, caller, new_key())
    with Session(db_engine) as session:
        first = session.scalars(select(IdempotencyRecord)).first()
        first.expires_at = utcnow() - timedelta(minutes=1)
        session.commit()
    assert purge_expired() == 1
    assert len(records(db_engine)) == 1
    assert purge_expired() == 0


def processing_record(engine, caller, key, body, *, lease: timedelta, path="/api/t/things"):
    with Session(engine) as session:
        session.add(IdempotencyRecord(
            subject_id=caller.subject_id, idempotency_key=key, endpoint=f"POST {path}",
            request_hash=body_hash(body), state="PROCESSING", lock_token=str(uuid.uuid4()),
            locked_until=utcnow() + lease, created_at=utcnow(),
            expires_at=utcnow() + timedelta(hours=24),
        ))
        session.commit()


def test_request_waits_then_conflicts_while_the_original_is_still_running(
    api, caller, db_engine, monkeypatch,
):
    monkeypatch.setattr(idempotency, "WAIT_TIMEOUT_SECONDS", 0.2)
    key = new_key()
    processing_record(db_engine, caller, key, {"name": "a", "tags": []}, lease=timedelta(minutes=1))
    response = post(api, caller, key)
    assert response.status_code == 409
    assert response.json()["code"] == "STATE_CONFLICT"
    assert response.headers["Retry-After"] == "1"
    assert hooks.calls == 0


def test_different_body_conflicts_immediately_even_while_processing(api, caller, db_engine):
    key = new_key()
    processing_record(db_engine, caller, key, {"name": "a", "tags": []}, lease=timedelta(minutes=1))
    response = post(api, caller, key, {"name": "other"})
    assert response.status_code == 409
    assert response.json()["code"] == "IDEMPOTENCY_KEY_REUSED"


def test_abandoned_lease_is_taken_over(api, caller, db_engine):
    key = new_key()
    processing_record(db_engine, caller, key, {"name": "a", "tags": []}, lease=-timedelta(seconds=1))
    response = post(api, caller, key)
    assert response.status_code == 201 and "Idempotent-Replayed" not in response.headers
    assert hooks.calls == 1
    assert records(db_engine)[0].state == "COMPLETED"


def test_losing_the_lease_mid_flight_aborts_without_committing(db_engine):
    caller = Caller(db_engine)
    key = new_key()

    def steal_lease_and_succeed(db):
        def work():
            with session_scope() as other:  # another request takes the key over
                record = other.scalars(select(IdempotencyRecord)).one()
                record.lock_token = str(uuid.uuid4())
            make_user(db, "WORKER", name="should-not-persist")
            return IdempotentResult(201, {"ok": True})

        return work

    with pytest.raises(ApiError) as caught, session_scope() as db:
        run_idempotent(
            db=db, principal=caller.principal, key=key, method="POST", path="/p",
            body={"a": 1}, handler=steal_lease_and_succeed(db),
        )
    assert caught.value.status_code == 409
    with Session(db_engine) as session:
        assert session.scalar(select(func.count()).select_from(User).where(
            User.name == "should-not-persist")) == 0


# --- concurrency -----------------------------------------------------------------------------

def run_concurrently(count, target):
    barrier = threading.Barrier(count)
    results = [None] * count

    def worker(index):
        barrier.wait()
        try:
            results[index] = target(index)
        except BaseException as error:  # noqa: BLE001 - reported to the assertion
            results[index] = error

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    return results


def test_concurrent_identical_requests_do_the_work_once(db_engine):
    caller = Caller(db_engine)
    key = new_key()
    executed = []

    def attempt(_index):
        with session_scope() as db:
            def work():
                executed.append(1)
                time.sleep(0.4)  # long enough for the other request to arrive meanwhile
                user = make_user(db, "WORKER", name="once")
                return IdempotentResult(201, {"id": user.id})

            response = run_idempotent(
                db=db, principal=caller.principal, key=key, method="POST", path="/p",
                body={"a": 1}, handler=work,
            )
            return response.status_code, response.body, response.headers.get("Idempotent-Replayed")

    results = run_concurrently(2, attempt)
    assert all(isinstance(r, tuple) for r in results), results
    assert len(executed) == 1
    assert {r[0] for r in results} == {201}
    assert results[0][1] == results[1][1]  # both got the same payload
    assert sorted(str(r[2]) for r in results) == ["None", "true"]
    with Session(db_engine) as session:
        assert session.scalar(select(func.count()).select_from(User).where(User.name == "once")) == 1


def test_many_concurrent_requests_still_run_once(db_engine):
    caller = Caller(db_engine)
    key = new_key()
    executed = []

    def attempt(_index):
        with session_scope() as db:
            def work():
                executed.append(1)
                time.sleep(0.2)
                return IdempotentResult(201, {"n": 1})

            return run_idempotent(
                db=db, principal=caller.principal, key=key, method="POST", path="/p",
                body={"a": 1}, handler=work,
            ).status_code

    results = run_concurrently(6, attempt)
    assert results == [201] * 6, results
    assert len(executed) == 1


def test_concurrent_same_key_with_different_bodies_never_runs_both(db_engine):
    caller = Caller(db_engine)
    key = new_key()
    executed = []

    def attempt(index):
        with session_scope() as db:
            def work():
                executed.append(index)
                time.sleep(0.3)
                return IdempotentResult(201, {"who": index})

            return run_idempotent(
                db=db, principal=caller.principal, key=key, method="POST", path="/p",
                body={"who": index}, handler=work,
            ).status_code

    results = run_concurrently(2, attempt)
    statuses = sorted(r.status_code if isinstance(r, ApiError) else r for r in results)
    assert statuses == [201, 409], results
    assert len(executed) == 1


def test_concurrent_http_requests_create_one_resource(db_engine):
    caller = Caller(db_engine)
    key = new_key()
    hooks.pause = 0.3
    client = TestClient(build_app())
    results = run_concurrently(2, lambda _i: post(client, caller, key))
    assert [r.status_code for r in results] == [201, 201]
    assert results[0].json() == results[1].json()
    assert hooks.calls == 1
    assert count_users(db_engine) == 2


def test_canonical_body_is_stable():
    assert canonical_body({"b": 1, "a": [1, 2]}) == canonical_body({"a": [1, 2], "b": 1})
    assert canonical_body({"a": 1}) != canonical_body({"a": 2})
    assert canonical_body(ThingIn(name="x")) == canonical_body({"name": "x", "tags": []})
    assert canonical_body(None) == "null"


# --- reservation errors: contention versus real failures -------------------------------------

class _DriverError(Exception):
    """Stands in for a DBAPI error: pymysql puts the MySQL errno in args[0]."""


def operational_error(errno: int | None, message: str = "driver failure") -> OperationalError:
    orig = _DriverError(errno, message) if errno is not None else _DriverError(message)
    return OperationalError("INSERT ...", {}, orig)


def fail_first_reservation(monkeypatch, error: Exception, *, always: bool = False) -> dict:
    """Make the reservation's first session_scope raise `error` (every one if `always`)."""
    real = idempotency.session_scope
    state = {"calls": 0}

    def scope():
        state["calls"] += 1
        if always or state["calls"] == 1:
            raise error
        return real()

    monkeypatch.setattr(idempotency, "session_scope", scope)
    return state


def no_sleeping(monkeypatch) -> list:
    """Record every wait; a connection failure must never wait."""
    sleeps: list[float] = []
    monkeypatch.setattr(idempotency.time, "sleep", sleeps.append)
    return sleeps


@pytest.mark.parametrize("errno", [2003, 2006, 2013, 1045, 1040, None])
def test_connection_errors_propagate_at_once_instead_of_becoming_a_conflict(
    db_engine, monkeypatch, errno,
):
    caller = Caller(db_engine)
    state = fail_first_reservation(monkeypatch, operational_error(errno), always=True)
    sleeps = no_sleeping(monkeypatch)
    started = time.monotonic()
    with pytest.raises(OperationalError), session_scope() as db:
        run_idempotent(
            db=db, principal=caller.principal, key=new_key(), method="POST", path="/p",
            body={"a": 1}, handler=lambda: pytest.fail("handler must not run"),
        )
    assert state["calls"] == 1 and sleeps == []
    assert time.monotonic() - started < 1


def test_connection_error_is_a_500_not_a_409(db_engine, monkeypatch):
    caller = Caller(db_engine)
    fail_first_reservation(monkeypatch, operational_error(2003), always=True)
    sleeps = no_sleeping(monkeypatch)
    api = TestClient(build_app(), raise_server_exceptions=False)
    response = post(api, caller, new_key())
    assert response.status_code == 500 and "STATE_CONFLICT" not in response.text
    assert hooks.calls == 0 and sleeps == []


@pytest.mark.parametrize("error", [
    operational_error(1205, "Lock wait timeout exceeded"),
    operational_error(1213, "Deadlock found"),
    OperationalError("INSERT", {}, sqlite3.OperationalError("database is locked")),
    OperationalError("INSERT", {}, sqlite3.OperationalError("database table is locked")),
    OperationalError("INSERT", {}, sqlite3.OperationalError("database is busy")),
], ids=["mysql-1205", "mysql-1213", "sqlite-locked", "sqlite-table-locked", "sqlite-busy"])
def test_lock_timeout_and_deadlock_are_treated_as_contention(api, caller, db_engine, monkeypatch, error):
    key = new_key()
    assert post(api, caller, key).status_code == 201
    state = fail_first_reservation(monkeypatch, error)
    response = post(api, caller, key)
    assert response.status_code == 201 and response.headers["Idempotent-Replayed"] == "true"
    assert hooks.calls == 1 and state["calls"] >= 2


def test_unique_conflict_is_still_contention(api, caller):
    key = new_key()
    assert post(api, caller, key).status_code == 201
    assert post(api, caller, key).headers["Idempotent-Replayed"] == "true"


def test_unrelated_sqlite_operational_errors_propagate(db_engine, monkeypatch):
    caller = Caller(db_engine)
    fail_first_reservation(
        monkeypatch, OperationalError("INSERT", {}, sqlite3.OperationalError("disk I/O error")),
        always=True,
    )
    with pytest.raises(OperationalError), session_scope() as db:
        run_idempotent(
            db=db, principal=caller.principal, key=new_key(), method="POST", path="/p",
            body={"a": 1}, handler=lambda: IdempotentResult(201),
        )


# --- the handler reads a fresh snapshot after waiting ----------------------------------------

@pytest.fixture
def mysql_caller_env(mysql_engine, monkeypatch):
    from app.db.models import Base

    yield mysql_engine
    with mysql_engine.begin() as connection:
        for table in reversed(Base.metadata.sorted_tables):
            connection.execute(table.delete())


@pytest.mark.mysql
def test_handler_sees_data_committed_while_it_waited_for_the_key(mysql_caller_env):
    """MySQL REPEATABLE READ: the snapshot opened when the request authenticated must not
    leak into the handler that runs after waiting for another request's lease."""
    engine = mysql_caller_env
    caller = Caller(engine)
    key = new_key()
    body = {"a": 1}
    processing_record(engine, caller, key, body, lease=timedelta(minutes=1), path="/p")
    seen = {}

    def late_request():
        with session_scope() as db:
            db.execute(select(func.count()).select_from(User))  # authentication's read
            def work():
                seen["late"] = db.scalar(
                    select(func.count()).select_from(User).where(User.name == "from-the-first")
                )
                return IdempotentResult(201, {"ok": True})

            run_idempotent(
                db=db, principal=caller.principal, key=key, method="POST", path="/p",
                body=body, handler=work,
            )

    worker = threading.Thread(target=late_request)
    worker.start()
    time.sleep(0.5)  # the late request is now polling behind the first one
    with Session(engine) as first:
        make_user(first, "WORKER", name="from-the-first")
        first.execute(update(IdempotencyRecord).values(locked_until=utcnow() - timedelta(seconds=1)))
        first.commit()  # the first request commits its data, then its lease lapses
    worker.join(timeout=30)
    assert not worker.is_alive()
    assert seen["late"] == 1


# --- replayed headers ------------------------------------------------------------------------

def header_app(headers):
    app = FastAPI()
    install_error_handlers(app)

    @app.post("/api/t/created")
    def created(body: ThingIn, owner: CsrfOwner, db: DbSession, key: IdempotencyKey):
        return run_idempotent(
            db=db, principal=owner, key=key, method="POST", path="/api/t/created", body=body,
            handler=lambda: IdempotentResult(201, {"id": "x"}, headers=headers),
        )

    return TestClient(app)


def test_allow_listed_headers_are_stored_and_replayed(db_engine):
    caller = Caller(db_engine)
    api = header_app({"Location": "/api/stores/abc", "ETag": '"v1"'})
    key = new_key()
    first = post(api, caller, key, path="/api/t/created")
    again = post(api, caller, key, path="/api/t/created")
    assert first.headers["location"] == "/api/stores/abc" and "Idempotent-Replayed" not in first.headers
    assert again.status_code == 201 and again.json() == {"id": "x"}
    assert again.headers["location"] == "/api/stores/abc" and again.headers["etag"] == '"v1"'
    assert again.headers["Idempotent-Replayed"] == "true"
    assert records(db_engine)[0].response_headers == {"location": "/api/stores/abc", "etag": '"v1"'}


@pytest.mark.parametrize("name", ["Set-Cookie", "set-cookie", "Authorization", "X-CSRF-Token", "X-Custom"])
def test_cookies_and_other_headers_cannot_be_stored(db_engine, name):
    with pytest.raises(ValueError, match="cannot be stored"):
        IdempotentResult(201, {}, headers={name: "jidan_session=secret-token"})


@pytest.mark.parametrize("value", ["", "a\r\nSet-Cookie: x=1", "x" * 3000, None])
def test_unsafe_header_values_are_rejected(value):
    with pytest.raises(ValueError, match="invalid value"):
        IdempotentResult(201, {}, headers={"Location": value})


def test_set_cookie_added_by_the_caller_is_neither_stored_nor_replayed(db_engine):
    caller = Caller(db_engine)
    app = FastAPI()
    install_error_handlers(app)

    @app.post("/api/t/cookie")
    def cookie(body: ThingIn, owner: CsrfOwner, db: DbSession, key: IdempotencyKey):
        response = run_idempotent(
            db=db, principal=owner, key=key, method="POST", path="/api/t/cookie", body=body,
            handler=lambda: IdempotentResult(201, {"ok": True}, headers={"Location": "/x"}),
        )
        if "Idempotent-Replayed" not in response.headers:  # only the run that did the work
            response.set_cookie("jidan_session", "secret-token-value")
        return response

    api = TestClient(app)
    key = new_key()
    first = post(api, caller, key, path="/api/t/cookie")
    again = post(api, caller, key, path="/api/t/cookie")
    assert "secret-token-value" in first.headers["set-cookie"]
    assert "set-cookie" not in again.headers and again.headers["location"] == "/x"
    with Session(db_engine) as session:
        row = session.scalars(select(IdempotencyRecord)).one()
        stored = f"{row.response_headers} {row.response_body}".lower()
        assert "cookie" not in stored and "secret-token-value" not in stored


def test_tampered_stored_headers_are_filtered_on_replay(db_engine):
    caller = Caller(db_engine)
    api = header_app({"Location": "/ok"})
    key = new_key()
    post(api, caller, key, path="/api/t/created")
    with Session(db_engine) as session:
        row = session.scalars(select(IdempotencyRecord)).one()
        row.response_headers = {"Set-Cookie": "evil=1", "location": "/ok", "x-other": "1"}
        session.commit()
    again = post(api, caller, key, path="/api/t/created")
    assert "set-cookie" not in again.headers and "x-other" not in again.headers
    assert again.headers["location"] == "/ok"


def test_results_without_headers_and_rows_without_the_column_value_still_replay(api, caller, db_engine):
    key = new_key()
    assert post(api, caller, key).status_code == 201
    with Session(db_engine) as session:
        session.execute(update(IdempotencyRecord).values(response_headers=None))
        session.commit()  # a row written before migration 0004
    again = post(api, caller, key)
    assert again.status_code == 201 and again.headers["Idempotent-Replayed"] == "true"
    assert "location" not in again.headers
