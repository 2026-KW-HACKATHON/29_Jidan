from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app import store_summary
from app.db.models import StoreAccessGrant, User
from tests.factories import NOW, make_invitation, make_worker
from tests.test_owner_stores import add_store
from tests.test_owner_stores import store_api as _store_api

store_api = _store_api


@pytest.fixture
def summary_api(store_api, monkeypatch):
    store_api.app.include_router(store_summary.router)
    monkeypatch.setattr(store_summary, "utcnow", lambda: NOW)
    return store_api


def grant(db, store, worker, **kwargs):
    invitation = make_invitation(db, store, accepted_at=NOW, accepted_by_worker_id=worker.id)
    row = StoreAccessGrant(store_id=store.id, worker_id=worker.id, invitation_id=invitation.id,
                           granted_at=NOW - timedelta(days=1), **kwargs)
    db.add(row)
    db.flush()


def test_summary_invitation_and_access_boundaries(summary_api, db_engine):
    with Session(db_engine) as db:
        owner = db.get(User, summary_api.owner_id)
        store, _ = add_store(db, owner, approval_status="APPROVED", approved_at=NOW)
        make_invitation(db, store)  # pending, no access deadline
        make_invitation(db, store, access_expires_at=NOW + timedelta(microseconds=1))
        for kwargs in (
            {"expires_at": NOW}, {"access_expires_at": NOW},
            {"canceled_at": NOW},
        ):
            make_invitation(db, store, created_at=NOW - timedelta(days=1), **kwargs)
        declined = make_worker(db)
        make_invitation(db, store, declined_at=NOW, declined_by_worker_id=declined.id)
        unlimited, expiring, extended, ended, future, suspended = [make_worker(db) for _ in range(6)]
        grant(db, store, unlimited)
        grant(db, store, unlimited, valid_until=NOW + timedelta(hours=1))
        grant(db, store, expiring, valid_until=NOW + timedelta(hours=24))
        grant(db, store, expiring, valid_until=NOW + timedelta(hours=1))
        grant(db, store, extended, valid_until=NOW + timedelta(hours=1))
        grant(db, store, extended, valid_until=NOW + timedelta(hours=24, microseconds=1))
        grant(db, store, ended, valid_until=NOW)
        grant(db, store, ended, revoked_at=NOW)
        invitation = make_invitation(db, store, accepted_at=NOW, accepted_by_worker_id=future.id)
        db.add(StoreAccessGrant(store_id=store.id, worker_id=future.id, invitation_id=invitation.id,
                               granted_at=NOW + timedelta(microseconds=1)))
        suspended.status = "SUSPENDED"
        grant(db, store, suspended)
        # Another store's access and invitations cannot enter the aggregates.
        foreign, _ = add_store(db, owner, approval_status="APPROVED", approved_at=NOW)
        make_invitation(db, foreign)
        grant(db, foreign, ended)
        db.commit()
        store_id = store.id
    response = summary_api.get(f"/api/stores/{store_id}/management-summary")
    assert response.status_code == 200
    assert response.json() == {
        "storeId": store_id, "pendingInvitationCount": 2, "activeWorkerCount": 3,
        "expiringWorkerCount": 1, "asOf": NOW.isoformat(),
    }


def test_summary_empty_and_approval_guard(summary_api, db_engine):
    with Session(db_engine) as db:
        owner = db.get(User, summary_api.owner_id)
        pending, _ = add_store(db, owner)
        approved, _ = add_store(db, owner, approval_status="APPROVED", approved_at=NOW)
        foreign, _ = add_store(db, None)
        db.commit()
        pending_id, approved_id, foreign_id = pending.id, approved.id, foreign.id
    assert summary_api.get(f"/api/stores/{pending_id}/management-summary").json()["code"] == "STORE_APPROVAL_REQUIRED"
    body = summary_api.get(f"/api/stores/{approved_id}/management-summary").json()
    assert body["pendingInvitationCount"] == body["activeWorkerCount"] == body["expiringWorkerCount"] == 0
    for store_id in (foreign_id, str(uuid4())):
        r = summary_api.get(f"/api/stores/{store_id}/management-summary")
        assert r.status_code == 404 and r.json()["code"] == "STORE_NOT_FOUND"
