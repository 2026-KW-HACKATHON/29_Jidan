import threading
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import auth
from app import stores as stores_module
from app.db.models import Store, StoreApprovalRequest, User
from app.errors import ApiError, ErrorCode
from tests.api_contract import login
from tests.factories import make_store_with_request, make_user, make_worker

APPROVED_AT = datetime(2026, 10, 2, 2, 0, tzinfo=UTC)
CREATED = datetime(2026, 10, 1, 0, 0, tzinfo=UTC)
ALL_PERMISSIONS = [
    "READ_STORE_STATUS", "MANAGE_STORE", "INVITE_WORKERS", "MANAGE_JOB_POSTINGS", "MANAGE_MANUALS",
]
MISSING = "00000000-0000-4000-8000-000000000000"


def _owner_with_stores(db_engine, count=0, **store_kwargs):
    with Session(db_engine) as db:
        owner = make_user(db, "OWNER")
        stores = [
            make_store_with_request(db, owner, created_at=CREATED + timedelta(minutes=i), **store_kwargs)
            for i in range(count)
        ]
        db.commit()
        return owner.id, [(store.id, request.id) for store, request in stores]


# --- GET /api/owners/me/stores ------------------------------------------------------------------

def test_list_includes_pending_and_approved_newest_first(api, db_engine):
    with Session(db_engine) as db:
        owner = make_user(db, "OWNER")
        pending, pending_request = make_store_with_request(db, owner, created_at=CREATED, detail_address="1층")
        approved, approved_request = make_store_with_request(
            db, owner, created_at=CREATED + timedelta(hours=1), approved_at=APPROVED_AT, detail_address=None,
        )
        other_owner = make_user(db, "OWNER")
        make_store_with_request(db, other_owner)
        db.commit()
        ids = owner.id, pending.id, pending_request.id, approved.id, approved_request.id
    owner_id, pending_id, pending_request_id, approved_id, approved_request_id = ids
    login(api, owner_id)
    response = api.get("/api/owners/me/stores")
    assert response.status_code == 200
    body = response.json()
    assert [item["id"] for item in body["items"]] == [approved_id, pending_id]
    first, second = body["items"]
    assert first["approvalRequestId"] == approved_request_id
    assert first["approvalStatus"] == "APPROVED"
    assert datetime.fromisoformat(first["approvedAt"]) == APPROVED_AT
    assert first["permissions"] == ALL_PERMISSIONS
    assert "detailAddress" not in first
    assert second["approvalRequestId"] == pending_request_id
    assert second["approvedAt"] is None and second["permissions"] == ["READ_STORE_STATUS"]
    assert second["detailAddress"] == "1층"
    assert (body["page"], body["size"], body["totalItems"], body["totalPages"]) == (0, 20, 2, 1)
    assert datetime.fromisoformat(body["asOf"]).tzinfo is not None


def test_list_orders_ties_by_id_desc_and_pages(api, db_engine):
    with Session(db_engine) as db:
        owner = make_user(db, "OWNER")
        ids = [make_store_with_request(db, owner, created_at=CREATED)[0].id for _ in range(5)]
        db.commit()
        owner_id = owner.id
    login(api, owner_id)
    expected = sorted(ids, reverse=True)
    seen = []
    for page in range(3):
        body = api.get(f"/api/owners/me/stores?page={page}&size=2").json()
        assert (body["totalItems"], body["totalPages"]) == (5, 3)
        seen += [item["id"] for item in body["items"]]
    assert seen == expected
    beyond = api.get("/api/owners/me/stores?page=3&size=2").json()
    assert beyond["items"] == [] and beyond["totalItems"] == 5


def test_list_empty(api, db_engine):
    owner_id, _ = _owner_with_stores(db_engine)
    login(api, owner_id)
    body = api.get("/api/owners/me/stores").json()
    assert body["items"] == [] and body["totalItems"] == 0 and body["totalPages"] == 0


@pytest.mark.parametrize("query", ["page=-1", "size=0", "size=101", "page=a", "page=1&page=2"])
def test_list_rejects_bad_pagination(api, db_engine, query):
    owner_id, _ = _owner_with_stores(db_engine)
    login(api, owner_id)
    response = api.get(f"/api/owners/me/stores?{query}")
    assert response.status_code == 422 and response.json()["code"] == "VALIDATION_ERROR"


def test_list_size_bounds(api, db_engine):
    owner_id, _ = _owner_with_stores(db_engine, 2)
    login(api, owner_id)
    assert len(api.get("/api/owners/me/stores?size=1").json()["items"]) == 1
    assert len(api.get("/api/owners/me/stores?size=100").json()["items"]) == 2


# --- GET /api/stores/{storeId} ----------------------------------------------------------------

def test_detail_pending_and_approved(api, db_engine):
    owner_id, [(pending_id, pending_request_id)] = _owner_with_stores(db_engine, 1)
    with Session(db_engine) as db:
        store, request = make_store_with_request(db, db.get(User, owner_id), approved_at=APPROVED_AT)
        db.commit()
        approved_id, approved_request_id = store.id, request.id
    login(api, owner_id)
    pending = api.get(f"/api/stores/{pending_id}")
    assert pending.status_code == 200
    assert pending.json()["id"] == pending_id and pending.json()["approvalRequestId"] == pending_request_id
    assert pending.json()["permissions"] == ["READ_STORE_STATUS"]
    approved = api.get(f"/api/stores/{approved_id.upper()}")
    assert approved.status_code == 200
    assert approved.json()["approvalRequestId"] == approved_request_id
    assert approved.json()["permissions"] == ALL_PERMISSIONS


def test_detail_hides_other_owner_and_missing(api, db_engine):
    _, [(other_store, _)] = _owner_with_stores(db_engine, 1)
    owner_id, _ = _owner_with_stores(db_engine)
    login(api, owner_id)
    for store_id in (other_store, MISSING):
        response = api.get(f"/api/stores/{store_id}")
        assert response.status_code == 404
        assert response.json()["code"] == "STORE_NOT_FOUND"
        assert response.json()["message"] == "매장을 찾을 수 없습니다."


def test_detail_rejects_malformed_store_id(api, db_engine):
    owner_id, _ = _owner_with_stores(db_engine)
    login(api, owner_id)
    response = api.get("/api/stores/not-a-uuid")
    assert response.status_code == 422
    assert response.json()["fieldErrors"][0]["field"] == "storeId"


# --- authentication shared by both reads --------------------------------------------------------

READS = ["/api/owners/me/stores", f"/api/stores/{MISSING}"]


@pytest.mark.parametrize("path", READS)
def test_reads_require_session(api, path):
    response = api.get(path)
    assert response.status_code == 401 and response.json()["code"] == "SESSION_EXPIRED"


@pytest.mark.parametrize("path", READS)
def test_reads_with_registration_session_only(api, db_engine, path):
    with Session(db_engine) as db:
        issued = auth.create_registration_session("reg-sub", "reg@example.com", db=db)
        db.commit()
    api.cookies.set(auth.REGISTRATION_COOKIE_NAME, issued.token)
    response = api.get(path)
    assert response.status_code == 401 and response.json()["code"] == "REGISTRATION_REQUIRED"


@pytest.mark.parametrize("path", READS)
def test_reads_forbid_workers(api, db_engine, path):
    with Session(db_engine) as db:
        worker = make_worker(db)
        db.commit()
        worker_id = worker.id
    login(api, worker_id)
    response = api.get(path)
    assert response.status_code == 403 and response.json()["code"] == "FORBIDDEN"


@pytest.mark.parametrize("path", READS)
def test_reads_reject_suspended_owner(api, db_engine, path):
    owner_id, _ = _owner_with_stores(db_engine, 1)
    login(api, owner_id)
    with Session(db_engine) as db:
        db.get(User, owner_id).status = "SUSPENDED"
        db.commit()
    response = api.get(path)
    assert response.status_code == 403 and response.json()["code"] == "ACCOUNT_SUSPENDED"


# --- POST /api/stores ---------------------------------------------------------------------------

NEW_STORE = {
    "name": "  명랑핫도그 광운대점 ", "industry": "RESTAURANT", "postalCode": "01897",
    "address": "서울특별시 노원구 광운로 20", "detailAddress": "1층",
    "businessRegistrationNumber": "1234567890", "phoneNumber": "029123456",
}


@pytest.fixture
def address_ok(monkeypatch):
    calls = []

    def verify(store):
        calls.append(store.address)
        return "서울특별시 노원구 광운로 20"

    monkeypatch.setattr(stores_module, "verify_store_address", verify)
    return calls


def _counts(db_engine):
    with Session(db_engine) as db:
        return (
            db.scalar(select(func.count()).select_from(Store)),
            db.scalar(select(func.count()).select_from(StoreApprovalRequest)),
        )


def test_create_store_pending_and_visible(api, db_engine, address_ok):
    with Session(db_engine) as db:
        owner = make_user(db, "OWNER")
        approved, _ = make_store_with_request(db, owner, approved_at=APPROVED_AT)
        db.commit()
        owner_id, approved_id = owner.id, approved.id
    session = login(api, owner_id)
    response = api.post("/api/stores", json=NEW_STORE, headers=session.headers(str(uuid.uuid4())))
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["name"] == "명랑핫도그 광운대점"
    assert body["approvalStatus"] == "PENDING" and body["approvedAt"] is None
    assert body["permissions"] == ["READ_STORE_STATUS"]
    assert body["id"] != body["approvalRequestId"]
    with Session(db_engine) as db:
        store = db.get(Store, body["id"])
        request = db.scalar(select(StoreApprovalRequest).where(StoreApprovalRequest.store_id == store.id))
        assert store.owner_id == owner_id and request.id == body["approvalRequestId"]
        assert request.status == "PENDING" and request.approved_at is None
        assert db.get(Store, approved_id).approval_status == "APPROVED"  # unaffected
    listed = api.get("/api/owners/me/stores").json()
    assert listed["items"][0]["id"] == body["id"]
    assert api.get(f"/api/stores/{body['id']}").status_code == 200
    stores_in_session = api.get("/api/auth/session").json()["user"]["stores"]
    assert {s["storeId"] for s in stores_in_session} == {approved_id, body["id"]}


def test_create_store_without_detail_address(api, db_engine, address_ok):
    owner_id, _ = _owner_with_stores(db_engine)
    session = login(api, owner_id)
    body = {key: value for key, value in NEW_STORE.items() if key != "detailAddress"}
    response = api.post("/api/stores", json=body, headers=session.headers(str(uuid.uuid4())))
    assert response.status_code == 201
    assert response.json()["detailAddress"] == ""


def test_create_store_replays_and_rejects_reused_key(api, db_engine, address_ok):
    owner_id, _ = _owner_with_stores(db_engine)
    session = login(api, owner_id)
    key = str(uuid.uuid4())
    first = api.post("/api/stores", json=NEW_STORE, headers=session.headers(key))
    replay = api.post("/api/stores", json=NEW_STORE, headers=session.headers(key.upper()))
    assert first.status_code == replay.status_code == 201
    assert replay.json() == first.json()
    assert replay.headers["Idempotent-Replayed"] == "true"
    assert len(address_ok) == 1
    other = api.post(
        "/api/stores", json={**NEW_STORE, "name": "다른 매장"}, headers=session.headers(key),
    )
    assert other.status_code == 409 and other.json()["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert _counts(db_engine) == (1, 1)


def test_create_store_duplicate_business_number(api, db_engine, address_ok):
    with Session(db_engine) as db:
        make_store_with_request(db, business_registration_number=NEW_STORE["businessRegistrationNumber"])
        db.commit()
    owner_id, _ = _owner_with_stores(db_engine)
    session = login(api, owner_id)
    response = api.post("/api/stores", json=NEW_STORE, headers=session.headers(str(uuid.uuid4())))
    assert response.status_code == 409
    assert response.json()["code"] == "STORE_ALREADY_REGISTERED"
    assert response.json()["message"] == "이미 등록된 매장입니다. 운영자에게 관리 권한을 문의해 주세요."
    assert _counts(db_engine) == (1, 1)


@pytest.mark.parametrize("error,status,code,field", [
    (ApiError(422, ErrorCode.STORE_OUTSIDE_SERVICE_AREA), 422, "STORE_OUTSIDE_SERVICE_AREA", None),
    (ApiError(422, ErrorCode.VALIDATION_ERROR, field_errors=[
        {"field": "store.postalCode", "code": "INVALID_FORMAT", "message": "주소와 우편번호를 확인해 주세요."},
    ]), 422, "VALIDATION_ERROR", "postalCode"),
    (ApiError(500, ErrorCode.INTERNAL_ERROR), 500, "INTERNAL_ERROR", None),
])
def test_create_store_address_failures_roll_back(api, db_engine, monkeypatch, error, status, code, field):
    def fail(store):
        raise error

    monkeypatch.setattr(stores_module, "verify_store_address", fail)
    owner_id, _ = _owner_with_stores(db_engine)
    session = login(api, owner_id)
    key = str(uuid.uuid4())
    response = api.post("/api/stores", json=NEW_STORE, headers=session.headers(key))
    assert response.status_code == status and response.json()["code"] == code
    if field:
        assert response.json()["fieldErrors"][0]["field"] == field
    assert _counts(db_engine) == (0, 0)
    # The key was released: the same request can be retried once the address check passes.
    monkeypatch.setattr(stores_module, "verify_store_address", lambda store: store.address)
    assert api.post("/api/stores", json=NEW_STORE, headers=session.headers(key)).status_code == 201


@pytest.mark.parametrize("change", [
    {"name": "   "}, {"name": "가" * 101}, {"industry": "BAR"}, {"postalCode": "1234"},
    {"businessRegistrationNumber": "123-45-67890"}, {"businessRegistrationNumber": "123456789"},
    {"phoneNumber": "02-912-3456"}, {"phoneNumber": "12345678901"}, {"address": ""},
    {"detailAddress": "가" * 201}, {"approvalStatus": "APPROVED"}, {"ownerId": MISSING},
    {"name": 1},
])
def test_create_store_validation(api, db_engine, address_ok, change):
    owner_id, _ = _owner_with_stores(db_engine)
    session = login(api, owner_id)
    response = api.post("/api/stores", json={**NEW_STORE, **change}, headers=session.headers(str(uuid.uuid4())))
    assert response.status_code == 422 and response.json()["code"] == "VALIDATION_ERROR"
    assert address_ok == []


@pytest.mark.parametrize("missing", ["name", "industry", "postalCode", "address", "businessRegistrationNumber", "phoneNumber"])
def test_create_store_requires_fields(api, db_engine, address_ok, missing):
    owner_id, _ = _owner_with_stores(db_engine)
    session = login(api, owner_id)
    body = {key: value for key, value in NEW_STORE.items() if key != missing}
    response = api.post("/api/stores", json=body, headers=session.headers(str(uuid.uuid4())))
    assert response.status_code == 422


def test_create_store_boundaries(api, db_engine, address_ok):
    owner_id, _ = _owner_with_stores(db_engine)
    session = login(api, owner_id)
    body = {**NEW_STORE, "name": "가" * 100, "detailAddress": "나" * 200, "phoneNumber": "01012345678"}
    response = api.post("/api/stores", json=body, headers=session.headers(str(uuid.uuid4())))
    assert response.status_code == 201


def test_create_store_malformed_json(api, db_engine, address_ok):
    owner_id, _ = _owner_with_stores(db_engine)
    session = login(api, owner_id)
    response = api.post(
        "/api/stores", content=b"{", headers={**session.headers(str(uuid.uuid4())), "Content-Type": "application/json"},
    )
    assert response.status_code == 400 and response.json()["code"] == "INVALID_REQUEST"


@pytest.mark.parametrize("key", [None, "", "not-a-uuid"])
def test_create_store_requires_idempotency_key(api, db_engine, address_ok, key):
    owner_id, _ = _owner_with_stores(db_engine)
    session = login(api, owner_id)
    headers = session.headers()
    if key is not None:
        headers["Idempotency-Key"] = key
    response = api.post("/api/stores", json=NEW_STORE, headers=headers)
    assert response.status_code == 422
    assert response.json()["fieldErrors"][0]["field"] == "Idempotency-Key"


@pytest.mark.parametrize("bad", ["origin", "token", "no_origin"])
def test_create_store_csrf(api, db_engine, address_ok, bad):
    owner_id, _ = _owner_with_stores(db_engine)
    session = login(api, owner_id)
    headers = session.headers(str(uuid.uuid4()))
    if bad == "origin":
        headers["Origin"] = "http://evil.test"
    elif bad == "token":
        headers["X-CSRF-Token"] = "wrong"
    else:
        del headers["Origin"]
    response = api.post("/api/stores", json=NEW_STORE, headers=headers)
    assert response.status_code == 403 and response.json()["code"] == "CSRF_INVALID"
    assert _counts(db_engine) == (0, 0)


def test_create_store_auth(api, db_engine, address_ok):
    response = api.post("/api/stores", json=NEW_STORE, headers={"Origin": "http://frontend.test"})
    assert response.status_code == 401
    with Session(db_engine) as db:
        worker = make_worker(db)
        db.commit()
        worker_id = worker.id
    session = login(api, worker_id)
    response = api.post("/api/stores", json=NEW_STORE, headers=session.headers(str(uuid.uuid4())))
    assert response.status_code == 403 and response.json()["code"] == "FORBIDDEN"
    assert _counts(db_engine) == (0, 0)


@pytest.mark.mysql
def test_concurrent_duplicate_business_number_creates_one_store(db_engine, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app
    from tests.api_contract import ORIGIN, validate_response

    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("ALLOWED_ORIGINS", ORIGIN)
    barrier = threading.Barrier(4)

    def verify(store):
        barrier.wait(5)  # every request passes the duplicate check before any inserts
        return store.address

    monkeypatch.setattr(stores_module, "verify_store_address", verify)
    owners = []
    with Session(db_engine) as db:
        for _ in range(4):
            owners.append(make_user(db, "OWNER").id)
        db.commit()
    results = []

    def submit(owner_id):
        with TestClient(app, raise_server_exceptions=False) as client:
            session = login(client, owner_id)
            response = client.post("/api/stores", json=NEW_STORE, headers=session.headers(str(uuid.uuid4())))
            validate_response(response)
            results.append((response.status_code, response.json().get("code")))

    threads = [threading.Thread(target=submit, args=(owner_id,)) for owner_id in owners]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    assert sorted(status for status, _ in results) == [201, 409, 409, 409]
    assert {code for status, code in results if status == 409} == {"STORE_ALREADY_REGISTERED"}
    assert _counts(db_engine) == (1, 1)



@pytest.mark.mysql
@pytest.mark.parametrize("waiters", [2, 4])
def test_waiters_survive_a_rolled_back_first_insert(db_engine, monkeypatch, waiters):
    """Requests waiting on a new business number deadlock when its inserter rolls back (1213).

    The victims retry and see the survivor's row, so the result is one 201 and 409s, never 500.
    """
    import time

    from fastapi.testclient import TestClient
    from sqlalchemy import event

    from app.main import app
    from tests.api_contract import ORIGIN, validate_response

    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("ALLOWED_ORIGINS", ORIGIN)
    verified = []
    monkeypatch.setattr(stores_module, "verify_store_address", lambda store: verified.append(1) or store.address)
    with Session(db_engine) as db:
        owners = [make_user(db, "OWNER").id for _ in range(waiters + 1)]
        db.commit()
    first = owners[0]
    inserted = threading.Event()

    def fail_first(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO stores") and first in str(parameters):
            inserted.set()
            time.sleep(1)  # the other requests now wait on the same UNIQUE key
            raise RuntimeError("injected failure after taking the key")

    results = []

    def submit(owner_id):
        with TestClient(app, raise_server_exceptions=False) as client:
            session = login(client, owner_id)
            if owner_id != first:
                assert inserted.wait(5)
                time.sleep(0.2)
            response = client.post("/api/stores", json=NEW_STORE, headers=session.headers(str(uuid.uuid4())))
            validate_response(response)
            results.append((owner_id == first, response.status_code, response.json().get("code")))

    event.listen(db_engine, "after_cursor_execute", fail_first)
    try:
        threads = [threading.Thread(target=submit, args=(owner_id,)) for owner_id in owners]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)
    finally:
        event.remove(db_engine, "after_cursor_execute", fail_first)
    assert (True, 500, "INTERNAL_ERROR") in results
    others = sorted((status, code) for is_first, status, code in results if not is_first)
    assert others == [(201, None)] + [(409, "STORE_ALREADY_REGISTERED")] * (waiters - 1)
    assert _counts(db_engine) == (1, 1)
    assert len(verified) == waiters + 1  # a retry reuses the external address check

@pytest.mark.mysql
def test_concurrent_same_key_creates_one_store(db_engine, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app
    from tests.api_contract import ORIGIN

    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("ALLOWED_ORIGINS", ORIGIN)
    monkeypatch.setattr(stores_module, "verify_store_address", lambda store: store.address)
    owner_id, _ = _owner_with_stores(db_engine)
    key = str(uuid.uuid4())
    results = []

    with TestClient(app) as probe:
        session = login(probe, owner_id)

    def submit():
        with TestClient(app, raise_server_exceptions=False) as client:
            from app.auth import SESSION_COOKIE_NAME
            client.cookies.set(SESSION_COOKIE_NAME, session.token)
            response = client.post("/api/stores", json=NEW_STORE, headers=session.headers(key))
            results.append((response.status_code, response.json()["id"]))

    threads = [threading.Thread(target=submit) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    assert [status for status, _ in results] == [201] * 4
    assert len({store_id for _, store_id in results}) == 1
    assert _counts(db_engine) == (1, 1)


# --- GET /api/stores/{storeId}/management-summary -------------------------------------------------

def _summary_path(store_id):
    return f"/api/stores/{store_id}/management-summary"


def test_summary_counts(api, db_engine, monkeypatch):
    from app.db import utcnow
    from tests.factories import make_invitation, make_regular_grant

    now = utcnow()
    monkeypatch.setattr(stores_module, "utcnow", lambda: now)  # pin the request's "now"
    hour = timedelta(hours=1)
    with Session(db_engine) as db:
        owner = make_user(db, "OWNER")
        store, _ = make_store_with_request(db, owner, approved_at=APPROVED_AT)
        other, _ = make_store_with_request(db, owner, approved_at=APPROVED_AT)
        workers = [make_worker(db) for _ in range(8)]
        past = now - 2 * hour
        # Pending: live, access period ends later; not counted: link expired, access period ended,
        # accepted/declined/canceled, another store.
        make_invitation(db, store, created_at=past, last_sent_at=past, expires_at=now + hour)
        make_invitation(db, store, created_at=past, last_sent_at=past, expires_at=now + hour,
                        access_expires_at=now + 2 * hour)
        make_invitation(db, store, created_at=now - 8 * 24 * hour, last_sent_at=past, expires_at=now)
        make_invitation(db, store, created_at=past, last_sent_at=past, expires_at=now + hour,
                        access_expires_at=now)
        make_invitation(db, store, created_at=past, last_sent_at=past, expires_at=now + hour,
                        access_expires_at=now - hour)
        make_invitation(db, store, created_at=past, last_sent_at=past, expires_at=now + hour,
                        canceled_at=now - hour)
        make_invitation(db, other, created_at=past, last_sent_at=past, expires_at=now + hour)
        # 0: open-ended -> active.  1: ends in 23h -> expiring.  2: ends in 23h + open-ended -> active.
        # 3: ends exactly in 24h -> expiring.  4: ends 1µs after 24h -> active.  5: revoked, 6: ended,
        # 7: starts later -> none.  Worker 0 also has another store's grant (not counted twice).
        make_regular_grant(db, store, workers[0], granted_at=past)
        make_regular_grant(db, other, workers[0], granted_at=past)
        make_regular_grant(db, store, workers[1], granted_at=past, valid_until=now + 23 * hour)
        make_regular_grant(db, store, workers[2], granted_at=past, valid_until=now + 23 * hour)
        make_regular_grant(db, store, workers[2], granted_at=past - hour)
        make_regular_grant(db, store, workers[3], granted_at=past, valid_until=now + 24 * hour)
        make_regular_grant(db, store, workers[4], granted_at=past,
                           valid_until=now + 24 * hour + timedelta(microseconds=1))
        make_regular_grant(db, store, workers[5], granted_at=past, revoked_at=now - hour)
        make_regular_grant(db, store, workers[6], granted_at=past - hour, valid_until=past)
        make_regular_grant(db, store, workers[7], granted_at=now + hour)
        db.commit()
        owner_id, store_id = owner.id, store.id
    login(api, owner_id)
    response = api.get(_summary_path(store_id))
    assert response.status_code == 200
    body = response.json()
    assert body["storeId"] == store_id
    assert body["pendingInvitationCount"] == 2
    assert body["activeWorkerCount"] == 5
    assert body["expiringWorkerCount"] == 2
    assert datetime.fromisoformat(body["asOf"]) == now


def test_summary_empty(api, db_engine):
    with Session(db_engine) as db:
        owner = make_user(db, "OWNER")
        store, _ = make_store_with_request(db, owner, approved_at=APPROVED_AT)
        db.commit()
        owner_id, store_id = owner.id, store.id
    login(api, owner_id)
    body = api.get(_summary_path(store_id)).json()
    assert (body["pendingInvitationCount"], body["activeWorkerCount"], body["expiringWorkerCount"]) == (0, 0, 0)


def test_summary_requires_approved_owned_store(api, db_engine):
    owner_id, [(pending_id, _)] = _owner_with_stores(db_engine, 1)
    _, [(foreign_id, _)] = _owner_with_stores(db_engine, 1)
    login(api, owner_id)
    response = api.get(_summary_path(pending_id))
    assert response.status_code == 403 and response.json()["code"] == "STORE_APPROVAL_REQUIRED"
    for store_id in (foreign_id, MISSING):
        response = api.get(_summary_path(store_id))
        assert response.status_code == 404 and response.json()["code"] == "STORE_NOT_FOUND"
    assert api.get(_summary_path("bad")).status_code == 422


def test_summary_auth(api, db_engine):
    assert api.get(_summary_path(MISSING)).json()["code"] == "SESSION_EXPIRED"
    with Session(db_engine) as db:
        worker = make_worker(db)
        db.commit()
        worker_id = worker.id
    login(api, worker_id)
    assert api.get(_summary_path(MISSING)).json()["code"] == "FORBIDDEN"
