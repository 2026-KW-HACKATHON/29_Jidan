from datetime import timedelta

import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app import store_summary
from app.db.models import Store, StoreAccessGrant, User
from tests.factories import NOW, make_invitation, make_worker
from tests.test_owner_stores import add_store
from tests.test_owner_stores import store_api as _store_api
from tests.test_store_summary import grant

store_api = _store_api


def test_owner_page_uses_one_snapshot(store_api, db_engine):
    if db_engine.dialect.name != "mysql":
        pytest.skip("Concurrent MySQL write required")
    with Session(db_engine) as db:
        store, _ = add_store(db, db.get(User, store_api.owner_id), name="before")
        db.commit()
        store_id = store.id
    injected = False
    def mutate(connection, cursor, statement, parameters, context, many):
        nonlocal injected
        if injected or "count(" not in statement.lower() or "FROM stores" not in statement:
            return
        injected = True
        with Session(db_engine) as db:
            db.get(Store, store_id).name = "after"
            add_store(db, db.get(User, store_api.owner_id))
            db.commit()
    event.listen(db_engine, "after_cursor_execute", mutate)
    try:
        result = store_api.get("/api/owners/me/stores").json()
    finally:
        event.remove(db_engine, "after_cursor_execute", mutate)
    assert injected and result["totalItems"] == len(result["items"]) == 1
    assert result["items"][0]["name"] == "before"
    current = store_api.get("/api/owners/me/stores").json()
    assert current["totalItems"] == 2
    assert next(s for s in current["items"] if s["id"] == store_id)["name"] == "after"


def test_summary_counts_share_snapshot_and_clock(store_api, db_engine, monkeypatch):
    if db_engine.dialect.name != "mysql":
        pytest.skip("Concurrent MySQL write required")
    store_api.app.include_router(store_summary.router)
    calls = []
    def now():
        calls.append(1)
        return NOW
    monkeypatch.setattr(store_summary, "utcnow", now)
    with Session(db_engine) as db:
        store, _ = add_store(db, db.get(User, store_api.owner_id),
                             approval_status="APPROVED", approved_at=NOW)
        worker = make_worker(db)
        grant(db, store, worker, valid_until=NOW + timedelta(hours=1))
        db.commit()
        store_id = store.id
    injected = False
    def mutate(connection, cursor, statement, parameters, context, many):
        nonlocal injected
        if injected or "count(" not in statement.lower() or "FROM store_invitations" not in statement:
            return
        injected = True
        with Session(db_engine) as db:
            db.scalar(select(StoreAccessGrant)).revoked_at = NOW
            make_invitation(db, db.get(Store, store_id))
            db.commit()
    event.listen(db_engine, "after_cursor_execute", mutate)
    try:
        body = store_api.get(f"/api/stores/{store_id}/management-summary").json()
    finally:
        event.remove(db_engine, "after_cursor_execute", mutate)
    assert injected and len(calls) == 1
    assert body["pendingInvitationCount"] == 0
    assert body["activeWorkerCount"] == body["expiringWorkerCount"] == 1
    current = store_api.get(f"/api/stores/{store_id}/management-summary").json()
    assert current["pendingInvitationCount"] == 1 and current["activeWorkerCount"] == 0
