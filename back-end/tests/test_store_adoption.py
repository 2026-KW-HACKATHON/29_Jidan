"""Adopt the upstream owner revalidation and ACTIVE WORKER summary contract.

The address provider is fake; API responses, commits, replay and MySQL row locks are real.
"""
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.orm import Session

from app import admin_password, stores
from app.db import utcnow
from app.db.models import IdempotencyRecord, Store, StoreApprovalRequest, User
from tests.api_contract import ORIGIN, login
from tests.factories import (
    make_regular_grant,
    make_store_with_request,
    make_temporary_grant,
    make_user,
    make_worker,
)
from tests.test_owner_stores import NEW_STORE


def counts(engine):
    with Session(engine) as db:
        return {model.__tablename__: db.scalar(select(func.count()).select_from(model))
                for model in (Store, StoreApprovalRequest, IdempotencyRecord)}


@pytest.mark.parametrize("field,value,code", [
    ("status", "SUSPENDED", "ACCOUNT_SUSPENDED"), ("role", "WORKER", "FORBIDDEN"),
])
def test_creation_rechecks_owner_after_address_and_same_key_recovers(api, db_engine, monkeypatch, field, value, code):
    with Session(db_engine) as db:
        owner = make_user(db, "OWNER")
        db.commit()
        owner_id = owner.id
    auth, key = login(api, owner_id), str(uuid.uuid4())
    before = counts(db_engine)

    def verify(body):
        with Session(db_engine) as other:
            setattr(other.get(User, owner_id), field, value)
            other.commit()
        return body.address

    monkeypatch.setattr(stores, "verify_store_address", verify)
    failed = api.post("/api/stores", json=NEW_STORE, headers=auth.headers(key))
    assert (failed.status_code, failed.json()["code"]) == (403, code)
    assert counts(db_engine) == before
    with Session(db_engine) as db:
        setattr(db.get(User, owner_id), field, "ACTIVE" if field == "status" else "OWNER")
        db.commit()
    monkeypatch.setattr(stores, "verify_store_address", lambda body: body.address)
    recovered = api.post("/api/stores", json=NEW_STORE, headers=auth.headers(key))
    replay = api.post("/api/stores", json=NEW_STORE, headers=auth.headers(key))
    assert recovered.status_code == replay.status_code == 201
    assert replay.json() == recovered.json() and replay.headers["Idempotent-Replayed"] == "true"
    assert counts(db_engine) == {table: count + 1 for table, count in before.items()}


def test_replay_rechecks_ownership_and_preserves_the_original_result(api, db_engine, monkeypatch):
    monkeypatch.setattr(stores, "verify_store_address", lambda body: body.address)
    with Session(db_engine) as db:
        owner, other = make_user(db, "OWNER"), make_user(db, "OWNER")
        db.commit()
        owner_id, other_id = owner.id, other.id
    auth, key = login(api, owner_id), str(uuid.uuid4())
    first = api.post("/api/stores", json=NEW_STORE, headers=auth.headers(key))
    assert first.status_code == 201
    store_id = first.json()["id"]
    with Session(db_engine) as db:
        db.get(Store, store_id).owner_id = other_id
        db.commit()
    before = counts(db_engine)
    denied = api.post("/api/stores", json=NEW_STORE, headers=auth.headers(key))
    assert (denied.status_code, denied.json()["code"]) == (403, "FORBIDDEN")
    assert "Idempotent-Replayed" not in denied.headers
    assert counts(db_engine) == before
    with Session(db_engine) as db:
        assert db.get(Store, store_id).owner_id == other_id
        db.get(Store, store_id).owner_id = owner_id
        db.commit()
    restored = api.post("/api/stores", json=NEW_STORE, headers=auth.headers(key))
    assert restored.status_code == 201 and restored.json() == first.json()
    assert restored.headers["Idempotent-Replayed"] == "true" and counts(db_engine) == before


def test_summary_counts_active_workers_once_across_regular_and_temporary_grants(api, db_engine, monkeypatch):
    now = utcnow()
    monkeypatch.setattr(stores, "utcnow", lambda: now)
    with Session(db_engine) as db:
        owner = make_user(db, "OWNER")
        store, _ = make_store_with_request(db, owner, approved_at=now)
        lasting, expiring, suspended, changed_role = [make_worker(db) for _ in range(4)]
        suspended.status = "SUSPENDED"
        changed_role.role = "OWNER"
        for worker in (lasting, expiring, suspended, changed_role):
            make_regular_grant(db, store, worker, granted_at=now - timedelta(hours=1),
                               valid_until=None if worker is lasting else now + timedelta(hours=12))
        make_temporary_grant(db, store, lasting, granted_at=now - timedelta(hours=1),
                             valid_until=now + timedelta(hours=12))
        db.commit()
        ids = owner.id, store.id
    login(api, ids[0])
    response = api.get(f"/api/stores/{ids[1]}/management-summary")
    assert response.status_code == 200
    assert (response.json()["activeWorkerCount"], response.json()["expiringWorkerCount"]) == (2, 1)


@pytest.mark.mysql
@pytest.mark.parametrize("db_engine", ["mysql"], indirect=True)
def test_owner_recheck_waits_for_a_concurrent_suspension_commit(api, db_engine, monkeypatch):
    monkeypatch.setattr(stores, "verify_store_address", lambda body: body.address)
    with Session(db_engine) as db:
        owner = make_user(db, "OWNER")
        db.commit()
        owner_id = owner.id
    auth, key = login(api, owner_id), str(uuid.uuid4())
    before = counts(db_engine)
    with Session(db_engine) as operator:
        row = operator.scalar(select(User).where(User.id == owner_id).with_for_update())
        row.status = "SUSPENDED"
        operator.flush()
        with ThreadPoolExecutor(1) as pool:
            pending = pool.submit(api.post, "/api/stores", json=NEW_STORE, headers=auth.headers(key))
            with pytest.raises(TimeoutError):
                pending.result(timeout=0.25)
            operator.commit()
            response = pending.result(timeout=15)
    assert (response.status_code, response.json()["code"]) == (403, "ACCOUNT_SUSPENDED")
    assert counts(db_engine) == before


@pytest.mark.mysql
@pytest.mark.parametrize("db_engine", ["mysql"], indirect=True)
def test_creation_does_not_block_approval_of_the_same_owners_other_store(api, db_engine, monkeypatch):
    monkeypatch.setattr(stores, "verify_store_address", lambda body: body.address)
    password = "shared-owner-lock-test"
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", admin_password.hash_password(password, log2_n=14))
    with Session(db_engine) as db:
        owner = make_user(db, "OWNER")
        old_store, approval = make_store_with_request(db, owner)
        db.commit()
        owner_id, approval_id, old_store_id = owner.id, approval.id, old_store.id
    auth = login(api, owner_id)
    reached, release = threading.Event(), threading.Event()

    def pause_before_insert(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO stores"):
            reached.set()
            assert release.wait(15)

    event.listen(db_engine, "before_cursor_execute", pause_before_insert)
    try:
        with ThreadPoolExecutor(2) as pool:
            creation = pool.submit(api.post, "/api/stores", json=NEW_STORE, headers=auth.headers(str(uuid.uuid4())))
            try:
                assert reached.wait(5), "creation did not reach its INSERT"
                approval = pool.submit(api.post, f"/api/admin/store-approval-requests/{approval_id}/approve",
                                       json={"password": password}, headers={"Origin": ORIGIN})
                assert approval.result(timeout=3).status_code == 200
            finally:
                release.set()
            assert creation.result(timeout=15).status_code == 201
    finally:
        release.set()
        event.remove(db_engine, "before_cursor_execute", pause_before_insert)
    with Session(db_engine) as db:
        assert db.get(Store, old_store_id).approval_status == "APPROVED"
