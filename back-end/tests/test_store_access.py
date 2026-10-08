import threading
import time
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.db.models import Store, StoreAccessGrant
from app.errors import ApiError, ErrorCode, install_error_handlers
from app.store_access import (
    StoreIdPath,
    grant_body,
    grant_status,
    has_worker_store_access,
    load_owned_store,
    normalize_uuid,
    require_worker_store_access,
)
from tests.factories import (
    make_regular_grant,
    make_store,
    make_temporary_grant,
    make_user,
    make_worker,
)

APPROVED_AT = datetime(2026, 10, 2, 2, 0, tzinfo=UTC)


def _stores(session):
    owner = make_user(session, "OWNER")
    other = make_user(session, "OWNER")
    approved = make_store(session, owner, approval_status="APPROVED", approved_at=APPROVED_AT)
    pending = make_store(session, owner)
    foreign = make_store(session, other, approval_status="APPROVED", approved_at=APPROVED_AT)
    foreign_pending = make_store(session, other)
    return owner, approved, pending, foreign, foreign_pending


def _error(call) -> ApiError:
    with pytest.raises(ApiError) as caught:
        call()
    return caught.value


def test_owned_approved_store_is_returned(session):
    owner, approved, pending, *_ = _stores(session)
    assert load_owned_store(session, owner.id, approved.id).id == approved.id
    assert load_owned_store(session, owner.id, pending.id, require_approved=False).id == pending.id
    assert load_owned_store(session, owner.id, approved.id.upper()).id == approved.id


def test_pending_store_requires_approval(session):
    owner, _, pending, *_ = _stores(session)
    error = _error(lambda: load_owned_store(session, owner.id, pending.id))
    assert (error.status_code, error.code) == (403, ErrorCode.STORE_APPROVAL_REQUIRED)


@pytest.mark.parametrize("which", ["foreign", "foreign_pending", "missing"])
def test_other_and_missing_stores_are_the_same_404(session, which):
    owner, _, _, foreign, foreign_pending = _stores(session)
    store_id = {
        "foreign": foreign.id, "foreign_pending": foreign_pending.id,
        "missing": "00000000-0000-4000-8000-000000000000",
    }[which]
    for require_approved in (True, False):
        error = _error(lambda flag=require_approved: load_owned_store(
            session, owner.id, store_id, require_approved=flag,
        ))
        assert (error.status_code, error.code, error.message) == (
            404, ErrorCode.STORE_NOT_FOUND, "매장을 찾을 수 없습니다.",
        )


def test_not_found_code_is_configurable(session):
    owner, *_ = _stores(session)
    error = _error(lambda: load_owned_store(
        session, owner.id, "00000000-0000-4000-8000-000000000000",
        not_found=ErrorCode.RESOURCE_NOT_FOUND,
    ))
    assert (error.status_code, error.code) == (404, ErrorCode.RESOURCE_NOT_FOUND)


def test_store_id_path_rejects_malformed_uuid():
    app = FastAPI()
    install_error_handlers(app)

    @app.get("/api/stores/{storeId}")
    def read(store_id: StoreIdPath) -> dict:
        return {"id": normalize_uuid(store_id)}

    client = TestClient(app)
    upper = "51C1C743-D377-4E7A-8449-94377EECFCE0"
    assert client.get(f"/api/stores/{upper}").json() == {"id": upper.lower()}
    for bad in ("not-a-uuid", "51c1c743d3774e7a844994377eecfce0", "51c1c743-d377-4e7a-8449-94377eecfce0x"):
        response = client.get(f"/api/stores/{bad}")
        assert response.status_code == 422
        assert response.json()["code"] == "VALIDATION_ERROR"
        assert response.json()["fieldErrors"][0]["field"] == "storeId"


@pytest.mark.mysql
@pytest.mark.parametrize("db_engine", ["mysql"], indirect=True)  # db_engine deletes every row afterwards
def test_lock_serializes_concurrent_store_changes(db_engine):
    mysql_engine = db_engine
    with Session(mysql_engine) as setup:
        owner, approved, *_ = _stores(setup)
        setup.commit()
        owner_id, store_id = owner.id, approved.id

    order: list[str] = []
    holding = threading.Event()

    def first():
        with Session(mysql_engine) as db:
            load_owned_store(db, owner_id, store_id, lock=True)
            holding.set()
            time.sleep(0.5)
            order.append("first-commit")
            db.commit()

    def second():
        holding.wait(5)
        with Session(mysql_engine) as db:
            load_owned_store(db, owner_id, store_id, lock=True)
            order.append("second-locked")
            db.commit()

    threads = [threading.Thread(target=first), threading.Thread(target=second)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert order == ["first-commit", "second-locked"]

    with Session(mysql_engine) as db:
        db.execute(update(Store).where(Store.id == store_id).values(name="잠금 후 변경"))
        db.commit()
        assert load_owned_store(db, owner_id, store_id).name == "잠금 후 변경"


# --- grant projection -------------------------------------------------------------------------

NOW = datetime(2026, 10, 5, 3, 0, tzinfo=UTC)
DAY = timedelta(hours=24)


@pytest.mark.parametrize("valid_until,revoked_at,expected", [
    (None, None, "ACTIVE"),
    (NOW + DAY + timedelta(microseconds=1), None, "ACTIVE"),
    (NOW + DAY, None, "EXPIRING"),
    (NOW + timedelta(seconds=1), None, "EXPIRING"),
    (NOW, None, "EXPIRED"),  # the end is exclusive
    (NOW - timedelta(days=1), None, "EXPIRED"),
    (None, NOW - timedelta(hours=1), "REVOKED"),
    (NOW - timedelta(days=1), NOW - timedelta(days=2), "REVOKED"),  # a manual end wins
])
def test_grant_status(valid_until, revoked_at, expected):
    grant = StoreAccessGrant(
        id="g", store_id="s", worker_id="w", invitation_id="i", granted_at=NOW - timedelta(days=3),
        valid_until=valid_until, revoked_at=revoked_at,
    )
    assert grant_status(grant, NOW) == expected
    body = grant_body(grant, NOW)
    assert body["type"] == "REGULAR" and body["status"] == expected
    usable = expected in ("ACTIVE", "EXPIRING")
    assert body["permissions"] == (["READ_MANUALS", "READ_CHECKLISTS", "USE_AI_QA"] if usable else [])


def test_temporary_grant_type():
    grant = StoreAccessGrant(id="g", store_id="s", worker_id="w", assignment_id="a", granted_at=NOW,
                             valid_until=NOW + timedelta(hours=4))
    assert grant_body(grant, NOW)["type"] == "TEMPORARY"


def test_locked_load_refreshes_a_row_read_earlier(session):
    owner, approved, *_ = _stores(session)
    load_owned_store(session, owner.id, approved.id)
    session.execute(update(Store).where(Store.id == approved.id).values(name="갱신됨").execution_options(
        synchronize_session=False,
    ))
    assert load_owned_store(session, owner.id, approved.id, lock=True).name == "갱신됨"


# --- worker access to store materials -------------------------------------------------------


def _worker_store(session):
    owner = make_user(session, "OWNER")
    store = make_store(session, owner, approval_status="APPROVED", approved_at=APPROVED_AT)
    worker = make_worker(session)
    return owner, store, worker


@pytest.mark.parametrize("grant,expected", [
    ({}, True),
    ({"valid_until": NOW + timedelta(seconds=1)}, True),
    ({"valid_until": NOW}, False),  # exclusive end
    ({"revoked_at": NOW - timedelta(hours=1)}, False),
    ({"granted_at": NOW + timedelta(seconds=1)}, False),  # not started
])
def test_worker_access_follows_the_grant_period(session, grant, expected):
    _, store, worker = _worker_store(session)
    values = {"granted_at": NOW - timedelta(days=1), **grant}
    make_regular_grant(session, store, worker, **values)
    assert has_worker_store_access(session, worker.id, store.id, NOW) is expected


def test_temporary_access_counts_and_any_valid_grant_is_enough(session):
    _, store, worker = _worker_store(session)
    make_regular_grant(session, store, worker, granted_at=NOW - timedelta(days=3), revoked_at=NOW - timedelta(days=1))
    assert not has_worker_store_access(session, worker.id, store.id, NOW)
    make_temporary_grant(session, store, worker, granted_at=NOW - timedelta(hours=1), valid_until=NOW + timedelta(hours=3))
    assert has_worker_store_access(session, worker.id, store.id, NOW)
    assert not has_worker_store_access(session, worker.id, store.id, NOW + timedelta(hours=3))


@pytest.mark.parametrize("change", ["worker_suspended", "store_pending", "owner_suspended", "other_store", "missing"])
def test_worker_access_rechecks_account_and_store(session, change):
    owner, store, worker = _worker_store(session)
    make_regular_grant(session, store, worker, granted_at=NOW - timedelta(days=1))
    store_id = store.id
    if change == "worker_suspended":
        worker.status = "SUSPENDED"
    elif change == "store_pending":
        store.approval_status, store.approved_at = "PENDING", None
    elif change == "owner_suspended":
        owner.status = "SUSPENDED"
    elif change == "other_store":
        store_id = make_store(session, owner, approval_status="APPROVED", approved_at=APPROVED_AT).id
    else:
        store_id = "00000000-0000-4000-8000-000000000000"
    session.flush()
    assert not has_worker_store_access(session, worker.id, store_id, NOW)
    error = _error(lambda: require_worker_store_access(session, worker.id, store_id, now=NOW))
    assert (error.status_code, error.code) == (404, ErrorCode.RESOURCE_NOT_FOUND)


def test_require_worker_store_access_returns_the_store(session):
    _, store, worker = _worker_store(session)
    make_regular_grant(session, store, worker, granted_at=NOW - timedelta(days=1))
    assert require_worker_store_access(session, worker.id, store.id.upper(), now=NOW).id == store.id
    error = _error(lambda: require_worker_store_access(
        session, worker.id, store.id, now=NOW - timedelta(days=2), status_code=403, code=ErrorCode.FORBIDDEN,
    ))
    assert (error.status_code, error.code) == (403, ErrorCode.FORBIDDEN)
