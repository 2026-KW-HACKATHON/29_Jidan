import logging
import threading
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app import admin_password
from app.db.models import Notification, Store, StoreApprovalRequest, User
from app.ratelimit import ADMIN_PASSWORD_IP_LIMIT, reset_all_limits
from tests.api_contract import ORIGIN, login
from tests.factories import make_store_with_request, make_user

PASSWORD = "  correct horse battery staple  "  # surrounding spaces are part of the password
SUBMITTED = datetime(2026, 10, 2, 10, 0, tzinfo=UTC)
APPROVED_AT = datetime(2026, 10, 2, 11, 0, tzinfo=UTC)
SEARCH = "/api/admin/store-approval-requests/search"


@pytest.fixture(scope="module")
def password_hash():
    return admin_password.hash_password(PASSWORD, log2_n=admin_password.MIN_LOG2_N)


@pytest.fixture(autouse=True)
def admin_env(monkeypatch, password_hash):
    monkeypatch.setenv(admin_password.HASH_ENV, password_hash)
    reset_all_limits()
    yield
    reset_all_limits()


@pytest.fixture
def api(api):
    """The admin page's requests: an allowed Origin and no member session or CSRF token."""
    api.headers["Origin"] = ORIGIN
    return api


# --- password hashing ---------------------------------------------------------------------------

def test_hash_round_trip_and_exact_comparison(password_hash):
    assert password_hash.startswith("scrypt$14$8$1$")
    assert PASSWORD not in password_hash
    assert admin_password.verify_password(PASSWORD)
    for wrong in (PASSWORD.strip(), PASSWORD + " ", PASSWORD.upper(), PASSWORD[:-1], ""):
        assert not admin_password.verify_password(wrong)
    assert admin_password.hash_password(PASSWORD, log2_n=14) != password_hash  # random salt


@pytest.mark.parametrize("stored", [
    "", "   ", "plain-password", "bcrypt$14$8$1$AAAA$BBBB", "scrypt$10$8$1$AAAAAAAAAAAAAAAAAAAAAA$" + "A" * 43,
    "scrypt$14$8$1$!!!$###", "scrypt$x$8$1$a$b",
    # Within MAX_LOG2_N but needs 256 MiB+ at r=8: scrypt would fail on every check.
    "scrypt$18$8$1$AAAAAAAAAAAAAAAAAAAAAA$" + "A" * 43,
])
def test_bad_configuration_is_rejected(monkeypatch, stored):
    monkeypatch.setenv(admin_password.HASH_ENV, stored)
    with pytest.raises(admin_password.AdminPasswordConfigError) as caught:
        admin_password.verify_password(PASSWORD)
    assert stored.strip() == "" or stored not in str(caught.value)


def test_hash_rejects_blank_password():
    with pytest.raises(ValueError):
        admin_password.hash_password("   ")


# --- POST /api/admin/store-approval-requests/search ---------------------------------------------

def _seed(db_engine):
    """Three requests: two PENDING (one tie on submittedAt) and one APPROVED, newest first."""
    with Session(db_engine) as db:
        owner = make_user(db, "OWNER", name="김민수", google_email="Owner@Example.com")
        _, req_a = make_store_with_request(db, owner, submitted_at=SUBMITTED, detail_address="1층")
        _, req_b = make_store_with_request(db, owner, submitted_at=SUBMITTED)
        approved, req_c = make_store_with_request(
            db, owner, submitted_at=SUBMITTED - timedelta(days=1), approved_at=APPROVED_AT,
        )
        db.commit()
        tie = sorted([req_a.id, req_b.id], reverse=True)
        return {"owner": owner.id, "pending": tie, "approved": req_c.id, "approved_store": approved.id}


def test_search_all_sorted_with_applicant_and_store(api, db_engine):
    ids = _seed(db_engine)
    response = api.post(SEARCH, json={"password": PASSWORD})
    assert response.status_code == 200, response.text
    body = response.json()
    assert [item["id"] for item in body["items"]] == [*ids["pending"], ids["approved"]]
    assert (body["page"], body["size"], body["totalItems"], body["totalPages"]) == (0, 20, 3, 1)
    approved = body["items"][2]
    assert approved["status"] == "APPROVED"
    assert datetime.fromisoformat(approved["approvedAt"]) == APPROVED_AT
    assert datetime.fromisoformat(approved["submittedAt"]) == SUBMITTED - timedelta(days=1)
    assert approved["store"]["id"] == ids["approved_store"]
    assert approved["applicant"] == {
        "id": ids["owner"], "name": "김민수", "email": "Owner@Example.com", "phoneNumber": "01012345678",
    }
    assert all(item["approvedAt"] is None for item in body["items"][:2])
    assert PASSWORD.strip() not in response.text


@pytest.mark.parametrize("status,expected", [("PENDING", 2), ("APPROVED", 1)])
def test_search_filters_by_status(api, db_engine, status, expected):
    _seed(db_engine)
    body = api.post(SEARCH, json={"password": PASSWORD, "status": status}).json()
    assert body["totalItems"] == expected
    assert {item["status"] for item in body["items"]} == {status}


def test_search_pages_and_empty(api, db_engine):
    ids = _seed(db_engine)
    first = api.post(SEARCH, json={"password": PASSWORD, "page": 0, "size": 2}).json()
    second = api.post(SEARCH, json={"password": PASSWORD, "page": 1, "size": 2}).json()
    beyond = api.post(SEARCH, json={"password": PASSWORD, "page": 5, "size": 2}).json()
    assert [i["id"] for i in first["items"] + second["items"]] == [*ids["pending"], ids["approved"]]
    assert first["totalPages"] == 2 and beyond["items"] == [] and beyond["totalItems"] == 3
    assert api.post(SEARCH, json={"password": PASSWORD, "size": 100}).status_code == 200


def test_search_empty_database(api):
    body = api.post(SEARCH, json={"password": PASSWORD}).json()
    assert body == {"items": [], "page": 0, "size": 20, "totalItems": 0, "totalPages": 0}


@pytest.mark.parametrize("body", [
    {}, {"password": ""}, {"password": "   "}, {"password": "x" * 1025}, {"password": 123},
    {"password": None}, {"password": PASSWORD, "status": "REJECTED"},
    {"password": PASSWORD, "page": -1}, {"password": PASSWORD, "size": 0},
    {"password": PASSWORD, "size": 101}, {"password": PASSWORD, "page": True},
    {"password": PASSWORD, "page": "1"}, {"password": PASSWORD, "page": 1_000_001},
    {"password": PASSWORD, "approvedBy": "x"},
])
def test_search_validation(api, db_engine, body):
    _seed(db_engine)
    response = api.post(SEARCH, json=body)
    assert response.status_code == 422 and response.json()["code"] == "VALIDATION_ERROR"
    assert "items" not in response.json()
    if isinstance(body.get("password"), str) and body["password"].strip():
        assert body["password"].strip() not in response.text


def test_search_malformed_json(api):
    response = api.post(SEARCH, content=b"{\"password\":", headers={"Content-Type": "application/json"})
    assert response.status_code == 400 and response.json()["code"] == "INVALID_REQUEST"


def test_wrong_password_reveals_nothing_and_is_not_logged(api, db_engine, caplog):
    _seed(db_engine)
    guess = "wrong-guess-1234"
    with caplog.at_level(logging.DEBUG):
        response = api.post(SEARCH, json={"password": guess})
        ok = api.post(SEARCH, json={"password": PASSWORD})
    assert response.status_code == 401
    assert response.json()["code"] == "ADMIN_PASSWORD_INVALID"
    assert response.json()["message"] == "관리자 인증에 실패했습니다."
    assert ok.status_code == 200
    for secret in (guess, PASSWORD.strip()):
        assert secret not in response.text
        assert secret not in caplog.text


def test_password_is_not_trimmed(api):
    assert api.post(SEARCH, json={"password": PASSWORD.strip()}).status_code == 401
    assert api.post(SEARCH, json={"password": PASSWORD}).status_code == 200


def test_failures_are_rate_limited_and_success_resets(api):
    for _ in range(ADMIN_PASSWORD_IP_LIMIT - 1):
        assert api.post(SEARCH, json={"password": "nope"}).status_code == 401
    assert api.post(SEARCH, json={"password": PASSWORD}).status_code == 200  # clears earlier failures
    for _ in range(ADMIN_PASSWORD_IP_LIMIT):
        assert api.post(SEARCH, json={"password": "nope"}).status_code == 401
    limited = api.post(SEARCH, json={"password": PASSWORD})
    assert limited.status_code == 429 and limited.json()["code"] == "RATE_LIMITED"
    assert int(limited.headers["Retry-After"]) >= 1
    assert "items" not in limited.json()


def test_validation_failures_count_against_the_limit(api):
    for _ in range(ADMIN_PASSWORD_IP_LIMIT):
        assert api.post(SEARCH, json={"password": ""}).status_code == 422
    assert api.post(SEARCH, json={"password": PASSWORD}).status_code == 429


@pytest.mark.parametrize("bad", [{"password": ""}, {"password": PASSWORD, "page": -1}])
def test_rejected_bodies_are_failures_that_a_success_forgives(api, bad):
    # The rate-limit dependency runs before body validation, so a 422 never reaches the endpoint.
    # It counts like a wrong password, and a later success clears it like one.
    for _ in range(ADMIN_PASSWORD_IP_LIMIT - 1):
        assert api.post(SEARCH, json=bad).status_code == 422
    assert api.post(SEARCH, json={"password": PASSWORD}).status_code == 200
    for _ in range(ADMIN_PASSWORD_IP_LIMIT):
        assert api.post(SEARCH, json=bad).status_code == 422
    assert api.post(SEARCH, json={"password": PASSWORD}).status_code == 429


LONE_SURROGATES = [b'{"password": "\\ud800"}', b'{"password": "abc\\udfff"}', b'{"password": "\\udc00\\ud800"}']


@pytest.mark.parametrize("body", LONE_SURROGATES)
def test_password_that_is_not_utf8_is_rejected_by_validation(api, caplog, body):
    # Valid JSON with a lone surrogate has no UTF-8 encoding. The body model rejects it before
    # the hash is derived (422, field error without the value), never a 500 from encoding.
    with caplog.at_level(logging.DEBUG):
        response = api.post(SEARCH, content=body, headers={"Content-Type": "application/json"})
    assert response.status_code == 422 and response.json()["code"] == "VALIDATION_ERROR"
    assert [error["field"] for error in response.json()["fieldErrors"]] == ["password"]
    assert "Unhandled" not in caplog.text and "items" not in response.json()
    assert "ud800" not in response.text.lower() and "udfff" not in response.text.lower()


def test_non_utf8_passwords_count_against_the_limit_and_a_success_forgives(api):
    raw = {"content": LONE_SURROGATES[0], "headers": {"Content-Type": "application/json"}}
    for _ in range(ADMIN_PASSWORD_IP_LIMIT - 1):
        assert api.post(SEARCH, **raw).status_code == 422
    assert api.post(SEARCH, json={"password": PASSWORD}).status_code == 200
    for _ in range(ADMIN_PASSWORD_IP_LIMIT):
        assert api.post(SEARCH, **raw).status_code == 422
    assert api.post(SEARCH, json={"password": PASSWORD}).status_code == 429


def test_unreadable_json_takes_no_attempt(api):
    # 400 is answered before dependencies run: no password was checked, so nothing is counted.
    for _ in range(ADMIN_PASSWORD_IP_LIMIT * 2):
        response = api.post(SEARCH, content=b"{", headers={"Content-Type": "application/json"})
        assert response.status_code == 400
    assert api.post(SEARCH, json={"password": PASSWORD}).status_code == 200


def test_successes_do_not_consume_the_limit(api):
    for _ in range(ADMIN_PASSWORD_IP_LIMIT * 2):
        assert api.post(SEARCH, json={"password": PASSWORD}).status_code == 200


def test_missing_configuration_is_internal_error(api, monkeypatch, caplog):
    monkeypatch.delenv(admin_password.HASH_ENV)
    with caplog.at_level(logging.DEBUG):
        response = api.post(SEARCH, json={"password": PASSWORD})
    assert response.status_code == 500 and response.json()["code"] == "INTERNAL_ERROR"
    assert PASSWORD.strip() not in caplog.text


def test_member_cookie_is_not_admin_authentication(api, db_engine):
    owner_id = _seed(db_engine)["owner"]
    login(api, owner_id)
    response = api.post(SEARCH, json={"password": "member-has-no-admin-rights"})
    assert response.status_code == 401


# --- POST /api/admin/store-approval-requests/{requestId}/approve --------------------------------

MISSING = "00000000-0000-4000-8000-000000000000"


def _approve(api, request_id, password=PASSWORD):
    return api.post(f"/api/admin/store-approval-requests/{request_id}/approve", json={"password": password})


def _pending(db_engine, role="OWNER"):
    with Session(db_engine) as db:
        owner = make_user(db, role)
        store, request = make_store_with_request(db, owner, submitted_at=SUBMITTED)
        other, other_request = make_store_with_request(db, owner, submitted_at=SUBMITTED)
        db.commit()
        return owner.id, store.id, request.id, other.id, other_request.id


def _state(db_engine, store_id, request_id):
    with Session(db_engine) as db:
        store = db.get(Store, store_id)
        request = db.get(StoreApprovalRequest, request_id)
        return store.approval_status, store.approved_at, request.status, request.approved_at


def test_approve_pending_request(api, db_engine):
    owner_id, store_id, request_id, other_id, other_request_id = _pending(db_engine)
    login(api, owner_id)  # a session issued before the approval (the admin call ignores cookies)
    assert api.get("/api/auth/session").json()["nextAction"] != "OWNER_HOME"
    response = _approve(api, request_id.upper())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] == request_id and body["status"] == "APPROVED"
    assert body["store"]["id"] == store_id and body["applicant"]["id"] == owner_id
    approved_at = datetime.fromisoformat(body["approvedAt"])
    assert _state(db_engine, store_id, request_id) == ("APPROVED", approved_at, "APPROVED", approved_at)
    assert _state(db_engine, other_id, other_request_id) == ("PENDING", None, "PENDING", None)
    assert PASSWORD.strip() not in response.text
    # The owner's existing session sees the new permissions on its next read.
    session = api.get("/api/auth/session").json()
    assert session["nextAction"] == "OWNER_HOME"
    permissions = {s["storeId"]: s["permissions"] for s in session["user"]["stores"]}
    assert len(permissions[store_id]) == 5 and permissions[other_id] == ["READ_STORE_STATUS"]
    assert api.get(f"/api/stores/{store_id}/management-summary").status_code == 200
    assert api.get(f"/api/stores/{other_id}/management-summary").status_code == 403


def test_approve_again_keeps_first_result(api, db_engine):
    _, store_id, request_id, *_ = _pending(db_engine)
    first = _approve(api, request_id).json()
    before = _state(db_engine, store_id, request_id)
    second = _approve(api, request_id)
    assert second.status_code == 200 and second.json() == first
    assert _state(db_engine, store_id, request_id) == before


def test_approve_unknown_request(api):
    response = _approve(api, MISSING)
    assert response.status_code == 404
    assert response.json()["code"] == "STORE_APPROVAL_REQUEST_NOT_FOUND"


def test_password_is_checked_before_the_request_is_looked_up(api, db_engine):
    _, store_id, request_id, *_ = _pending(db_engine)
    for target in (request_id, MISSING):
        response = _approve(api, target, password="wrong")
        assert response.status_code == 401 and response.json()["code"] == "ADMIN_PASSWORD_INVALID"
        assert target not in response.text
    assert _state(db_engine, store_id, request_id) == ("PENDING", None, "PENDING", None)


def test_approve_with_non_utf8_password_changes_nothing(api, db_engine):
    _, store_id, request_id, *_ = _pending(db_engine)
    response = api.post(f"/api/admin/store-approval-requests/{request_id}/approve",
                        content=LONE_SURROGATES[1], headers={"Content-Type": "application/json"})
    assert response.status_code == 422 and response.json()["code"] == "VALIDATION_ERROR"
    assert _state(db_engine, store_id, request_id) == ("PENDING", None, "PENDING", None)


@pytest.mark.parametrize("body", [{}, {"password": ""}, {"password": PASSWORD, "approvedAt": "2026-10-02T11:00:00Z"},
                                  {"password": PASSWORD, "status": "APPROVED"}])
def test_approve_validation(api, db_engine, body):
    _, store_id, request_id, *_ = _pending(db_engine)
    response = api.post(f"/api/admin/store-approval-requests/{request_id}/approve", json=body)
    assert response.status_code == 422
    assert _state(db_engine, store_id, request_id) == ("PENDING", None, "PENDING", None)


def test_approve_malformed_request_id(api):
    response = _approve(api, "not-a-uuid")
    assert response.status_code == 422
    assert response.json()["fieldErrors"][0]["field"] == "requestId"


def test_approve_rejects_non_owner_applicant(api, db_engine):
    _, store_id, request_id, *_ = _pending(db_engine, role="WORKER")
    response = _approve(api, request_id)
    assert response.status_code == 409 and response.json()["code"] == "STORE_APPROVAL_NOT_ALLOWED"
    assert _state(db_engine, store_id, request_id) == ("PENDING", None, "PENDING", None)


def _notification_count(db_engine):
    with Session(db_engine) as db:
        return len(db.scalars(select(Notification)).all())


def _break_state(db_engine, owner_id, store_id, request_id, case):
    with Session(db_engine) as db:
        owner, store = db.get(User, owner_id), db.get(Store, store_id)
        request = db.get(StoreApprovalRequest, request_id)
        if case == "suspended-owner":
            owner.status = "SUSPENDED"
        elif case == "store-approved-request-pending":
            store.approval_status, store.approved_at = "APPROVED", APPROVED_AT
        elif case == "request-approved-store-pending":
            request.status, request.approved_at = "APPROVED", APPROVED_AT
        elif case == "approved-at-mismatch":
            store.approval_status, store.approved_at = "APPROVED", APPROVED_AT
            request.status, request.approved_at = "APPROVED", APPROVED_AT + timedelta(seconds=1)
        db.commit()


STATE_CASES = ["suspended-owner", "store-approved-request-pending", "request-approved-store-pending",
               "approved-at-mismatch"]


@pytest.mark.parametrize("case", STATE_CASES)
def test_approve_refuses_inconsistent_state_without_changes(api, db_engine, case):
    # The evaluation reproduced the first two over HTTP: 200 and both rows changed.
    owner_id, store_id, request_id, *_ = _pending(db_engine)
    _break_state(db_engine, owner_id, store_id, request_id, case)
    before = _state(db_engine, store_id, request_id)
    response = _approve(api, request_id)
    assert response.status_code == 409 and response.json()["code"] == "STORE_APPROVAL_NOT_ALLOWED"
    assert request_id not in response.text
    assert _state(db_engine, store_id, request_id) == before
    assert _notification_count(db_engine) == 0
    with Session(db_engine) as db:
        assert db.get(User, owner_id).status == ("SUSPENDED" if case == "suspended-owner" else "ACTIVE")


def test_approve_refused_after_the_owner_role_changed(api, db_engine):
    owner_id, store_id, request_id, *_ = _pending(db_engine)
    with Session(db_engine) as db:
        db.get(User, owner_id).role = "WORKER"
        db.commit()
    response = _approve(api, request_id)
    assert response.status_code == 409 and response.json()["code"] == "STORE_APPROVAL_NOT_ALLOWED"
    assert _state(db_engine, store_id, request_id) == ("PENDING", None, "PENDING", None)
    assert _notification_count(db_engine) == 0


def test_already_approved_is_idempotent_only_when_consistent(api, db_engine):
    owner_id, store_id, request_id, *_ = _pending(db_engine)
    first = _approve(api, request_id)
    assert first.status_code == 200 and _notification_count(db_engine) == 1
    approved = _state(db_engine, store_id, request_id)
    assert approved[0] == approved[2] == "APPROVED" and approved[1] == approved[3]
    again = _approve(api, request_id)
    assert again.status_code == 200 and again.json() == first.json()
    assert _state(db_engine, store_id, request_id) == approved and _notification_count(db_engine) == 1
    # An approved request whose owner was suspended afterwards is not reported as approved again.
    with Session(db_engine) as db:
        db.get(User, owner_id).status = "SUSPENDED"
        db.commit()
    refused = _approve(api, request_id)
    assert refused.status_code == 409 and refused.json()["code"] == "STORE_APPROVAL_NOT_ALLOWED"
    assert _state(db_engine, store_id, request_id) == approved and _notification_count(db_engine) == 1


@pytest.mark.mysql
@pytest.mark.parametrize("db_engine", ["mysql"], indirect=True)
def test_approval_reads_the_owner_status_committed_while_it_waited(api, db_engine):
    """An operator suspending the owner in a transaction that holds the user row: the approval
    waits for it and then sees SUSPENDED (a stale snapshot would approve)."""
    from concurrent.futures import ThreadPoolExecutor

    owner_id, store_id, request_id, *_ = _pending(db_engine)
    with Session(db_engine) as operator:
        operator.scalar(select(User).where(User.id == owner_id).with_for_update())
        operator.get(User, owner_id).status = "SUSPENDED"
        operator.flush()
        with ThreadPoolExecutor(1) as pool:
            pending = pool.submit(_approve, api, request_id)
            with pytest.raises(TimeoutError):
                pending.result(timeout=1)  # blocked on the owner row
            operator.commit()
            response = pending.result(timeout=30)
    assert response.status_code == 409 and response.json()["code"] == "STORE_APPROVAL_NOT_ALLOWED"
    assert _state(db_engine, store_id, request_id) == ("PENDING", None, "PENDING", None)
    assert _notification_count(db_engine) == 0


def test_approve_rolls_back_when_store_update_fails(api, db_engine):
    _, store_id, request_id, *_ = _pending(db_engine)

    def fail(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("UPDATE STORES"):
            raise RuntimeError("injected failure")

    event.listen(db_engine, "before_cursor_execute", fail)
    try:
        response = _approve(api, request_id)
    finally:
        event.remove(db_engine, "before_cursor_execute", fail)
    assert response.status_code == 500 and response.json()["code"] == "INTERNAL_ERROR"
    assert _state(db_engine, store_id, request_id) == ("PENDING", None, "PENDING", None)
    assert _approve(api, request_id).status_code == 200


def test_store_changed_after_the_check_rolls_the_request_back(api, db_engine):
    # A store already APPROVED by the time its update runs (a change outside this API) must not
    # leave the request APPROVED alone: 409 and the whole approval is rolled back.
    _, store_id, request_id, *_ = _pending(db_engine)
    fired = []

    def approve_store_first(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("UPDATE STORES") and not fired:
            fired.append(True)
            cursor.execute(
                "UPDATE stores SET approval_status = 'APPROVED', approved_at = created_at WHERE id = "
                + ("%s" if conn.dialect.name == "mysql" else "?"), (store_id,),
            )

    event.listen(db_engine, "before_cursor_execute", approve_store_first)
    try:
        response = _approve(api, request_id)
    finally:
        event.remove(db_engine, "before_cursor_execute", approve_store_first)
    assert fired
    assert response.status_code == 409 and response.json()["code"] == "STORE_APPROVAL_NOT_ALLOWED"
    assert _state(db_engine, store_id, request_id) == ("PENDING", None, "PENDING", None)
    assert _notification_count(db_engine) == 0


@pytest.mark.parametrize("db_engine", ["sqlite"], indirect=True)
def test_approval_that_lost_a_race_answers_from_the_winner(api, db_engine):
    # SQLite has no row locks: another approval commits after this one read the PENDING request
    # but before it reads the store. The rows read then disagree only because of that approval,
    # so this one answers 200 from it (not 409) and records nothing more. MySQL waits on the lock.
    _, store_id, request_id, *_ = _pending(db_engine)
    fired = []

    def other_approval_commits(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT") and "FROM stores" in statement and not fired:
            fired.append(True)
            with Session(db_engine) as other:
                other.get(StoreApprovalRequest, request_id).status = "APPROVED"
                other.get(StoreApprovalRequest, request_id).approved_at = APPROVED_AT
                other.get(Store, store_id).approval_status = "APPROVED"
                other.get(Store, store_id).approved_at = APPROVED_AT
                other.commit()

    event.listen(db_engine, "before_cursor_execute", other_approval_commits)
    try:
        response = _approve(api, request_id)
    finally:
        event.remove(db_engine, "before_cursor_execute", other_approval_commits)
    assert fired
    assert response.status_code == 200, response.text
    assert datetime.fromisoformat(response.json()["approvedAt"]) == APPROVED_AT
    assert _state(db_engine, store_id, request_id) == ("APPROVED", APPROVED_AT, "APPROVED", APPROVED_AT)
    assert _notification_count(db_engine) == 0  # the winner (out of band here) owns the notification


@pytest.mark.mysql
@pytest.mark.parametrize("db_engine", ["mysql"], indirect=True)
def test_shared_owner_lock_does_not_block_approval(api, db_engine):
    """The applicant lock is shared: another shared lock on the owner (an approval of one of
    the owner's other stores) does not make this approval wait."""
    from concurrent.futures import ThreadPoolExecutor

    owner_id, store_id, request_id, *_ = _pending(db_engine)
    with Session(db_engine) as holder, ThreadPoolExecutor(1) as pool:
        holder.scalar(select(User).where(User.id == owner_id).with_for_update(read=True))
        pending = pool.submit(_approve, api, request_id)
        try:
            response = pending.result(timeout=3)  # an exclusive owner lock would wait here
        finally:
            holder.commit()
            pending.result(timeout=30)
    assert response.status_code == 200, response.text
    assert _state(db_engine, store_id, request_id)[0] == "APPROVED"


@pytest.mark.mysql
@pytest.mark.parametrize("db_engine", ["mysql"], indirect=True)
def test_deadlock_victim_approval_is_retried(api, db_engine, caplog):
    """An outside transaction locks the owner, then the store: the reverse of the approval's
    order. MySQL rolls back the lighter transaction (the approval, which has written nothing)
    with 1213; the approval runs again after the outside transaction and succeeds once."""
    from concurrent.futures import ThreadPoolExecutor

    from sqlalchemy import update

    owner_id, store_id, request_id, *_ = _pending(db_engine)
    with Session(db_engine) as db:
        # Rows the outside transaction changes, so that MySQL picks the approval as the victim.
        fillers = [make_user(db, "WORKER").id for _ in range(20)]
        db.commit()
    with caplog.at_level(logging.WARNING, logger="jidan.admin"), Session(db_engine) as outside, \
            ThreadPoolExecutor(1) as pool:
        for user_id in fillers:
            outside.execute(update(User).where(User.id == user_id).values(name="일괄변경"))
        outside.execute(update(User).where(User.id == owner_id).values(name="이름변경"))
        pending = pool.submit(_approve, api, request_id)
        with pytest.raises(TimeoutError):
            pending.result(timeout=1)  # the approval holds request and store, waits for the owner
        outside.scalar(select(Store).where(Store.id == store_id).with_for_update())  # 1213 for the approval
        outside.commit()
        response = pending.result(timeout=30)
    assert response.status_code == 200, response.text
    assert "Admin approval retried after lock contention: attempt=1" in caplog.text
    approved_at = datetime.fromisoformat(response.json()["approvedAt"])
    assert _state(db_engine, store_id, request_id) == ("APPROVED", approved_at, "APPROVED", approved_at)
    assert _notification_count(db_engine) == 1


def test_lock_contention_retries_are_bounded(api, db_engine, monkeypatch, caplog):
    """Contention on every attempt: the approval stops after APPROVAL_ATTEMPTS, answers the
    documented 500 and changes nothing."""
    from sqlalchemy.exc import OperationalError

    from app import store_approvals

    monkeypatch.setattr(store_approvals, "APPROVAL_RETRY_DELAY_SECONDS", 0)
    _, store_id, request_id, *_ = _pending(db_engine)
    calls = []

    def contended(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("UPDATE STORES"):
            calls.append(statement)
            raise OperationalError(statement, parameters, Exception(1213, "Deadlock found"))

    event.listen(db_engine, "before_cursor_execute", contended)
    try:
        with caplog.at_level(logging.INFO, logger="jidan.admin"):
            response = _approve(api, request_id)
    finally:
        event.remove(db_engine, "before_cursor_execute", contended)
    assert response.status_code == 500 and response.json()["code"] == "INTERNAL_ERROR"
    assert len(calls) == store_approvals.APPROVAL_ATTEMPTS
    assert f"approval_request_id={request_id} result=INTERNAL_ERROR" in caplog.text
    assert _state(db_engine, store_id, request_id) == ("PENDING", None, "PENDING", None)
    assert _notification_count(db_engine) == 0


def test_approve_rate_limited(api, db_engine):
    _, _, request_id, *_ = _pending(db_engine)
    for _ in range(ADMIN_PASSWORD_IP_LIMIT):
        assert _approve(api, request_id, password="nope").status_code == 401
    limited = _approve(api, request_id)
    assert limited.status_code == 429 and "Retry-After" in limited.headers


@pytest.mark.mysql
def test_concurrent_approvals_apply_once(db_engine, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app
    from tests.api_contract import validate_response

    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("ALLOWED_ORIGINS", ORIGIN)
    _, store_id, request_id, *_ = _pending(db_engine)
    # As many concurrent requests as one client's admin budget: each reserves a slot first.
    barrier = threading.Barrier(ADMIN_PASSWORD_IP_LIMIT)
    results = []

    def approve():
        with TestClient(app, raise_server_exceptions=False, headers={"Origin": ORIGIN}) as client:
            barrier.wait(5)
            response = _approve(client, request_id)
            validate_response(response)
            results.append((response.status_code, response.json()["approvedAt"]))

    threads = [threading.Thread(target=approve) for _ in range(ADMIN_PASSWORD_IP_LIMIT)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    assert [status for status, _ in results] == [200] * ADMIN_PASSWORD_IP_LIMIT
    assert len({approved_at for _, approved_at in results}) == 1
    store_status, store_at, request_status, request_at = _state(db_engine, store_id, request_id)
    assert store_status == request_status == "APPROVED"
    assert store_at == request_at == datetime.fromisoformat(results[0][1])
    with Session(db_engine) as db:
        assert db.scalar(select(StoreApprovalRequest.status).where(StoreApprovalRequest.id == request_id)) == "APPROVED"
    assert _notification_count(db_engine) == 1


def test_operation_records_name_action_request_and_result_only(api, db_engine, caplog):
    owner_id, _, request_id, *_ = _pending(db_engine)
    with Session(db_engine) as db:
        owner = db.get(User, owner_id)
        private = [owner.name, owner.google_email, owner.phone_number, PASSWORD.strip()]
        conflict_owner = make_user(db, "WORKER")
        _, conflict_request = make_store_with_request(db, conflict_owner, submitted_at=SUBMITTED)
        db.commit()
        conflict_id = conflict_request.id
    with caplog.at_level(logging.INFO, logger="jidan.admin"):
        statuses = [
            api.post(SEARCH, json={"password": PASSWORD, "status": "PENDING"}).status_code,
            api.post(SEARCH, json={"password": "wrong"}).status_code,
            _approve(api, request_id).status_code,
            _approve(api, request_id).status_code,
            _approve(api, MISSING).status_code,
            _approve(api, conflict_id).status_code,
        ]
        for _ in range(ADMIN_PASSWORD_IP_LIMIT):
            _approve(api, request_id, password="wrong")
        statuses.append(_approve(api, request_id).status_code)
    assert statuses == [200, 401, 200, 200, 404, 409, 429]
    records = [r.getMessage() for r in caplog.records if r.name == "jidan.admin"]
    expected = [
        "action=search approval_request_id=- result=OK status=PENDING page=0 count=",
        "action=search approval_request_id=- result=ADMIN_PASSWORD_INVALID",
        f"action=approve approval_request_id={request_id} result=APPROVED",
        f"action=approve approval_request_id={request_id} result=ALREADY_APPROVED",
        f"action=approve approval_request_id={MISSING} result=STORE_APPROVAL_REQUEST_NOT_FOUND",
        f"action=approve approval_request_id={conflict_id} result=STORE_APPROVAL_NOT_ALLOWED",
        f"path=/api/admin/store-approval-requests/{request_id}/approve result=RATE_LIMITED",
    ]
    for fragment in expected:
        assert any(fragment in message for message in records), (fragment, records)
    assert sum("result=ADMIN_PASSWORD_INVALID" in m for m in records) == 1 + ADMIN_PASSWORD_IP_LIMIT
    for secret in private:
        assert secret not in caplog.text


def test_unexpected_failure_is_recorded_as_internal_error(api, db_engine, caplog):
    _, _, request_id, *_ = _pending(db_engine)

    def fail(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("UPDATE STORES"):
            raise RuntimeError("injected failure")

    event.listen(db_engine, "before_cursor_execute", fail)
    try:
        with caplog.at_level(logging.INFO, logger="jidan.admin"):
            assert _approve(api, request_id).status_code == 500
    finally:
        event.remove(db_engine, "before_cursor_execute", fail)
    assert f"action=approve approval_request_id={request_id} result=INTERNAL_ERROR" in caplog.text
    assert "result=APPROVED" not in caplog.text  # rolled back, so not recorded as approved


def test_password_hashing_runs_at_most_two_at_once(api, monkeypatch):
    import time
    from concurrent.futures import ThreadPoolExecutor

    original = admin_password._derive
    lock = threading.Lock()
    running = peak = 0

    def slow(*args):
        nonlocal running, peak
        with lock:
            running += 1
            peak = max(peak, running)
        time.sleep(0.2)
        try:
            return original(*args)
        finally:
            with lock:
                running -= 1

    monkeypatch.setattr(admin_password, "_derive", slow)
    barrier = threading.Barrier(ADMIN_PASSWORD_IP_LIMIT)

    def search(_):
        barrier.wait(5)
        return api.post(SEARCH, json={"password": PASSWORD}).status_code

    with ThreadPoolExecutor(ADMIN_PASSWORD_IP_LIMIT) as pool:
        statuses = list(pool.map(search, range(ADMIN_PASSWORD_IP_LIMIT)))
    assert statuses == [200] * ADMIN_PASSWORD_IP_LIMIT
    assert peak == admin_password.MAX_CONCURRENT_HASHES


def test_busy_hashing_is_429_and_not_a_failed_guess(api, monkeypatch):
    monkeypatch.setattr(admin_password, "HASH_WAIT_SECONDS", 0.01)
    held = [admin_password._hash_slots.acquire(timeout=1) for _ in range(admin_password.MAX_CONCURRENT_HASHES)]
    try:
        for _ in range(ADMIN_PASSWORD_IP_LIMIT + 1):
            busy = api.post(SEARCH, json={"password": "wrong"})
            assert busy.status_code == 429 and busy.json()["code"] == "RATE_LIMITED"
            assert busy.headers["Retry-After"] == "1"
    finally:
        for acquired in held:
            if acquired:
                admin_password._hash_slots.release()
    # The busy refusals used no budget: a full budget of wrong guesses is still answered 401.
    for _ in range(ADMIN_PASSWORD_IP_LIMIT):
        assert api.post(SEARCH, json={"password": "wrong"}).status_code == 401
    assert api.post(SEARCH, json={"password": PASSWORD}).status_code == 429


# --- allowed Origin ------------------------------------------------------------------------------
# The admin page is a browser page like any other: a password request from a foreign page must
# not be answered. No member session exists, so the Origin is the whole CSRF check (no token).

BAD_ORIGINS = {
    "missing": [],
    "foreign": [("Origin", "https://evil.example")],
    "duplicate": [("Origin", ORIGIN), ("Origin", ORIGIN)],
    "with-path": [("Origin", ORIGIN + "/")],
    "null": [("Origin", "null")],
    "no-scheme": [("Origin", "frontend.test")],
}


def _post_with_origin(api, path, body, origin):
    del api.headers["Origin"]
    return api.post(path, json=body, headers=origin)


def _approve_path(request_id):
    return f"/api/admin/store-approval-requests/{request_id}/approve"


@pytest.mark.parametrize("origin", BAD_ORIGINS.values(), ids=BAD_ORIGINS.keys())
@pytest.mark.parametrize("password", [PASSWORD, "wrong"], ids=["right-password", "wrong-password"])
def test_search_needs_the_allowed_origin(api, db_engine, origin, password):
    _seed(db_engine)
    response = _post_with_origin(api, SEARCH, {"password": password}, origin)
    assert response.status_code == 403 and response.json()["code"] == "CSRF_INVALID"
    assert "items" not in response.json()


@pytest.mark.parametrize("origin", BAD_ORIGINS.values(), ids=BAD_ORIGINS.keys())
@pytest.mark.parametrize("password", [PASSWORD, "wrong"], ids=["right-password", "wrong-password"])
def test_approve_needs_the_allowed_origin(api, db_engine, origin, password):
    _, store_id, request_id, *_ = _pending(db_engine)
    response = _post_with_origin(api, _approve_path(request_id), {"password": password}, origin)
    assert response.status_code == 403 and response.json()["code"] == "CSRF_INVALID"
    assert request_id not in response.text
    assert _state(db_engine, store_id, request_id) == ("PENDING", None, "PENDING", None)
    with Session(db_engine) as db:
        assert db.scalars(select(Notification)).all() == []


def test_allowed_origin_keeps_password_results(api, db_engine):
    _, _, request_id, *_ = _pending(db_engine)
    assert api.post(SEARCH, json={"password": "wrong"}).status_code == 401
    assert _approve(api, request_id, password="wrong").status_code == 401
    assert api.post(SEARCH, json={"password": PASSWORD}).status_code == 200
    assert _approve(api, request_id).status_code == 200


def test_origin_is_checked_before_the_body(api, db_engine):
    _, _, request_id, *_ = _pending(db_engine)
    # An unreadable body is still 400: the JSON is read before any dependency runs.
    del api.headers["Origin"]
    broken = api.post(SEARCH, content=b"{", headers={"Content-Type": "application/json"})
    assert broken.status_code == 400 and broken.json()["code"] == "INVALID_REQUEST"
    # A readable but invalid body from a foreign page is refused for its Origin, not validated.
    for path in (SEARCH, _approve_path(request_id)):
        response = api.post(path, json={"password": ""}, headers={"Origin": "https://evil.example"})
        assert response.status_code == 403 and response.json()["code"] == "CSRF_INVALID"
    assert api.post(_approve_path("not-a-uuid"), json={"password": PASSWORD}).status_code == 403


def test_origin_refusals_take_no_password_attempt(api):
    # The password is never checked, so a foreign page cannot spend the admin's guess budget.
    for _ in range(ADMIN_PASSWORD_IP_LIMIT * 2):
        assert api.post(SEARCH, json={"password": "wrong"}, headers={"Origin": "https://evil.example"}).status_code == 403
    for _ in range(ADMIN_PASSWORD_IP_LIMIT):
        assert api.post(SEARCH, json={"password": "wrong"}).status_code == 401
    assert api.post(SEARCH, json={"password": PASSWORD}).status_code == 429


def test_origin_refusal_is_recorded_without_the_password(api, db_engine, caplog):
    _, _, request_id, *_ = _pending(db_engine)
    with caplog.at_level(logging.INFO, logger="jidan.admin"):
        for path in (SEARCH, _approve_path(request_id)):
            response = api.post(path, json={"password": PASSWORD}, headers={"Origin": "https://evil.example"})
            assert response.status_code == 403
    records = [r.getMessage() for r in caplog.records if r.name == "jidan.admin"]
    for path in (SEARCH, _approve_path(request_id)):
        assert f"Admin operation refused: path={path} result=CSRF_INVALID" in records, records
    assert PASSWORD.strip() not in caplog.text and "evil.example" not in caplog.text
