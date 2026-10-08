"""Ending a worker's access while other stores grant and invite (MySQL row and gap locks).

Range UPDATE / FOR UPDATE on the `(store_id, worker_id)` grant index and the
`(store_id, invited_email)` invitation index lock the gap up to the next key, which can belong
to another store. Grants inserted by invitation acceptance or shift confirmation and invitations
inserted by owners of that neighbouring store then wait on it, and deadlock (MySQL 1213 -> 500)
once both sides hold what the other needs. Every response here must be the expected 2xx.
"""
import threading
from collections import Counter
from datetime import time, timedelta

import pytest
from sqlalchemy.orm import Session

from app.db import utcnow
from app.db.models import Store, User
from tests.api_contract import ORIGIN, login
from tests.factories import make_application, make_job, make_request, make_worker
from tests.invitation_helpers import configure_mail, make_world, new_key, seed_invitation

STORES = 6
ROUNDS = 10


def _pending_work_request(db_engine, store_id, worker_id, day: int) -> str:
    """A REQUESTED application with a live PENDING work request for a shift `day` days ahead."""
    now = utcnow()
    with Session(db_engine) as db:
        store = db.get(Store, store_id)
        job = make_job(db, store, work_date=now.date() + timedelta(days=3 + day),
                       start_time=time(9, 0), end_time=time(10, 0))
        application = make_application(db, job, db.get(User, worker_id), status="REQUESTED")
        request = make_request(db, application, store.owner_id, requested_at=now, expires_at=now + timedelta(hours=1))
        db.commit()
        return request.id


@pytest.mark.mysql
@pytest.mark.parametrize("db_engine", ["mysql"], indirect=True)
def test_revocation_alongside_grants_and_invitations_across_stores(db_engine, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app

    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("ALLOWED_ORIGINS", ORIGIN)
    configure_mail(monkeypatch)
    worlds = [make_world(db_engine, worker_email=f"regular{i}@example.com") for i in range(STORES)]
    temps = {}
    with Session(db_engine) as db:
        for world in worlds:
            temps[world.store_id] = make_worker(db).id
        db.commit()
    requests = {
        world.store_id: [_pending_work_request(db_engine, world.store_id, temps[world.store_id], n) for n in range(ROUNDS)]
        for world in worlds
    }
    statuses = Counter()
    errors = []
    barrier = threading.Barrier(STORES * 2)

    def access_loop(world):
        temp_id = temps[world.store_id]
        try:
            with TestClient(app, raise_server_exceptions=False) as regular, \
                    TestClient(app, raise_server_exceptions=False) as temp, \
                    TestClient(app, raise_server_exceptions=False) as owner:
                regular_session = login(regular, world.worker_id)
                temp_session = login(temp, temp_id)
                owner_session = login(owner, world.owner_id)
                workers = f"/api/stores/{world.store_id}/workers"
                barrier.wait(15)
                for n in range(ROUNDS):
                    _, token = seed_invitation(db_engine, world.store_id, email=world.worker_email)
                    accepted = regular.post("/api/store-invitations/accept", json={"token": token},
                                            headers=regular_session.headers())
                    statuses["accept", accepted.status_code] += 1
                    confirmed = temp.post(f"/api/users/me/work-requests/{requests[world.store_id][n]}/response",
                                          json={"expectedRevision": 1, "decision": "ACCEPT"},
                                          headers=temp_session.headers(new_key()))
                    statuses["confirm", confirmed.status_code] += 1
                    for worker_id in (world.worker_id, temp_id):
                        revoked = owner.delete(f"{workers}/{worker_id}/access", headers=owner_session.headers())
                        statuses["revoke", revoked.status_code] += 1
        except Exception as error:  # noqa: BLE001 - surfaced by the assertion below
            errors.append(repr(error))

    def invite_loop(world):
        try:
            with TestClient(app, raise_server_exceptions=False) as client:
                session = login(client, world.owner_id)
                base = f"/api/stores/{world.store_id}/invitations"
                barrier.wait(15)
                for n in range(ROUNDS * 2):
                    created = client.post(base, json={"email": f"g{n}-{world.store_id[:8]}@example.com"},
                                          headers=session.headers(new_key()))
                    statuses["create", created.status_code] += 1
        except Exception as error:  # noqa: BLE001
            errors.append(repr(error))

    threads = [threading.Thread(target=loop, args=(world,)) for world in worlds for loop in (access_loop, invite_loop)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(180)
    assert errors == []
    expected = {("accept", 200), ("confirm", 200), ("revoke", 204), ("create", 201)}
    assert set(statuses) == expected, statuses
    assert statuses["accept", 200] == statuses["confirm", 200] == STORES * ROUNDS
    assert statuses["revoke", 204] == STORES * ROUNDS * 2
