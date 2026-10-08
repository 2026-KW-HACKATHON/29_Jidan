"""Cross-store concurrency of invitation and access changes (MySQL row and gap locks)."""
import threading
from collections import Counter

import pytest

from tests.api_contract import ORIGIN, login
from tests.invitation_helpers import configure_mail, make_world, new_key, seed_invitation

STORES = 8
ROUNDS = 15


@pytest.mark.mysql
@pytest.mark.parametrize("db_engine", ["mysql"], indirect=True)
def test_stores_change_invitations_and_access_concurrently_without_deadlock(db_engine, monkeypatch):
    # Each store's owner creates, resends and cancels while its worker accepts and is revoked.
    # A range UPDATE on the outbox invitation_id index used to deadlock resends of different
    # stores with each other's outbox INSERT (MySQL 1213 -> 500).
    from fastapi.testclient import TestClient

    from app.main import app

    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("ALLOWED_ORIGINS", ORIGIN)
    configure_mail(monkeypatch)
    worlds = [make_world(db_engine, worker_email=f"worker{i}@example.com") for i in range(STORES)]
    statuses = Counter()
    barrier = threading.Barrier(STORES * 2)

    def owner_loop(world):
        with TestClient(app, raise_server_exceptions=False) as client:
            session = login(client, world.owner_id)
            base = f"/api/stores/{world.store_id}/invitations"
            barrier.wait(10)
            for n in range(ROUNDS):
                created = client.post(base, json={"email": f"guest{n}-{world.store_id[:8]}@example.com"},
                                      headers=session.headers(new_key()))
                statuses["create", created.status_code] += 1
                invitation_id = created.json()["invitation"]["id"]
                resent = client.post(f"{base}/{invitation_id}/resend", headers=session.headers(new_key()))
                statuses["resend", resent.status_code] += 1
                cancelled = client.post(f"{base}/{invitation_id}/cancel", headers=session.headers())
                statuses["cancel", cancelled.status_code] += 1

    def worker_loop(world):
        with TestClient(app, raise_server_exceptions=False) as worker, \
                TestClient(app, raise_server_exceptions=False) as owner:
            worker_session, owner_session = login(worker, world.worker_id), login(owner, world.owner_id)
            barrier.wait(10)
            for _ in range(ROUNDS):
                _, token = seed_invitation(db_engine, world.store_id, email=world.worker_email)
                accepted = worker.post("/api/store-invitations/accept", json={"token": token},
                                       headers=worker_session.headers())
                statuses["accept", accepted.status_code] += 1
                revoked = owner.delete(f"/api/stores/{world.store_id}/workers/{world.worker_id}/access",
                                       headers=owner_session.headers())
                statuses["revoke", revoked.status_code] += 1

    threads = [threading.Thread(target=loop, args=(world,)) for world in worlds for loop in (owner_loop, worker_loop)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(120)
    expected = {("create", 201), ("resend", 200), ("cancel", 200), ("accept", 200), ("revoke", 204)}
    assert set(statuses) == expected, statuses
    assert all(count == STORES * ROUNDS for count in statuses.values()), statuses


@pytest.mark.mysql
@pytest.mark.parametrize("db_engine", ["mysql"], indirect=True)
def test_mail_delivery_runs_alongside_invitation_changes_without_deadlock(db_engine, monkeypatch):
    # Delivery runs (periodic job, BackgroundTask, other processes) claim and discard outbox rows
    # while owners create, resend and cancel. A claim that locked index ranges or read
    # store_invitations with shared locks could deadlock those requests' outbox INSERTs. The
    # delivery errors themselves are invisible to clients (they run after the response), so
    # this test calls the delivery directly and records what it raises.
    from fastapi.testclient import TestClient

    from app import invitation_mail
    from app.main import app

    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("ALLOWED_ORIGINS", ORIGIN)
    configure_mail(monkeypatch)
    worlds = [make_world(db_engine, worker_email=f"mailer{i}@example.com") for i in range(STORES)]
    statuses = Counter()
    delivery_errors = []
    done = threading.Event()
    barrier = threading.Barrier(STORES + 3)

    def owner_loop(world):
        with TestClient(app, raise_server_exceptions=False) as client:
            session = login(client, world.owner_id)
            base = f"/api/stores/{world.store_id}/invitations"
            barrier.wait(10)
            for n in range(ROUNDS):
                created = client.post(base, json={"email": f"m{n}-{world.store_id[:8]}@example.com"},
                                      headers=session.headers(new_key()))
                statuses["create", created.status_code] += 1
                if created.status_code != 201:
                    continue
                invitation_id = created.json()["invitation"]["id"]
                resent = client.post(f"{base}/{invitation_id}/resend", headers=session.headers(new_key()))
                statuses["resend", resent.status_code] += 1
                if n % 2:
                    cancelled = client.post(f"{base}/{invitation_id}/cancel", headers=session.headers())
                    statuses["cancel", cancelled.status_code] += 1

    def delivery_loop():
        barrier.wait(10)
        while not done.is_set():
            try:
                invitation_mail.deliver_queued_mail(limit=5)
            except Exception as error:  # noqa: BLE001 - collected for the assertion
                delivery_errors.append(type(error).__name__)

    owners = [threading.Thread(target=owner_loop, args=(world,)) for world in worlds]
    deliverers = [threading.Thread(target=delivery_loop) for _ in range(3)]
    for thread in owners + deliverers:
        thread.start()
    for thread in owners:
        thread.join(120)
    done.set()
    for thread in deliverers:
        thread.join(30)
    assert delivery_errors == []
    assert set(statuses) <= {("create", 201), ("resend", 200), ("cancel", 200)}, statuses


@pytest.mark.mysql
@pytest.mark.parametrize("db_engine", ["mysql"], indirect=True)
def test_create_waiting_on_a_held_idempotency_key_answers_409_not_500(db_engine, monkeypatch):
    """Another transaction holds the same key's uncommitted reservation longer than the lock wait.

    The reservation INSERT waits on the unique key, gets 1205, and run_idempotent answers 409
    STATE_CONFLICT with Retry-After. Before the session lock wait was set, the 3 s socket
    timeout ended that wait as 2013 "Lost connection" first: a 500.
    """
    import hashlib
    import time
    from datetime import timedelta

    from fastapi.testclient import TestClient
    from sqlalchemy.orm import Session

    from app.db import get_engine, reset_engine, utcnow
    from app.db.models import IdempotencyRecord, User
    from app.main import app

    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("ALLOWED_ORIGINS", ORIGIN)
    monkeypatch.setenv("DB_LOCK_WAIT_TIMEOUT_SECONDS", "1")
    monkeypatch.setenv("DB_READ_TIMEOUT_SECONDS", "3")
    configure_mail(monkeypatch)
    reset_engine()
    world = make_world(db_engine)
    key = new_key()
    with Session(get_engine()) as holder:
        google_sub = holder.get(User, world.owner_id).google_sub
        now = utcnow()
        holder.add(IdempotencyRecord(
            subject_id=hashlib.sha256(google_sub.encode()).hexdigest(), idempotency_key=key,
            endpoint="POST /elsewhere", request_hash="0" * 64, state="PROCESSING", lock_token=new_key(),
            locked_until=now + timedelta(minutes=1), created_at=now, expires_at=now + timedelta(days=1),
        ))
        holder.flush()  # uncommitted: the unique key stays locked until the rollback below
        try:
            with TestClient(app, raise_server_exceptions=False) as client:
                session = login(client, world.owner_id)
                started = time.monotonic()
                response = client.post(f"/api/stores/{world.store_id}/invitations",
                                       json={"email": "held@example.com"}, headers=session.headers(key))
                elapsed = time.monotonic() - started
        finally:
            holder.rollback()
    assert response.status_code == 409, response.text
    assert response.json()["code"] == "STATE_CONFLICT" and response.headers["Retry-After"] == "1"
    assert elapsed < 20
    reset_engine()
