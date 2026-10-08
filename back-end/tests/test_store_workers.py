import threading
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import store_workers
from app.db import utcnow
from app.db.models import InvitationMailOutbox, Store, StoreAccessGrant, StoreInvitation, User
from tests.api_contract import login
from tests.factories import make_regular_grant, make_temporary_grant, make_worker
from tests.invitation_helpers import (
    MISSING,
    WORKER_EMAIL,
    configure_mail,
    make_world,
    new_key,
    seed_invitation,
)

PERMISSIONS = ["READ_MANUALS", "READ_CHECKLISTS", "USE_AI_QA"]


@pytest.fixture(autouse=True)
def mail_env(monkeypatch):
    return configure_mail(monkeypatch)


@pytest.fixture
def now(monkeypatch):
    value = utcnow()
    monkeypatch.setattr(store_workers, "utcnow", lambda: value)
    return value


def _path(world, suffix=""):
    return f"/api/stores/{world.store_id}/workers{suffix}"


def _team(db_engine, now):
    """A store with workers in every state; returns (world, {label: worker_id})."""
    world = make_world(db_engine)
    hour, day = timedelta(hours=1), timedelta(days=1)
    with Session(db_engine) as db:
        store = db.get(Store, world.store_id)
        names = {}

        def worker(label, name):
            user = make_worker(db)
            user.name = name
            user.google_email = f"{label}@example.com"
            names[label] = user
            return user

        # regular: open-ended (ACTIVE).  mixed: open-ended regular + temp ending in 2h (ACTIVE).
        # temp: temp ending in 2h only (EXPIRING).  limited: regular ending in 2 days (ACTIVE).
        # revoked: revoked regular (ENDED).  expired: regular ended an hour ago (ENDED).
        make_regular_grant(db, store, worker("regular", "김지수"), granted_at=now - 5 * day)
        mixed = worker("mixed", "이민준")
        make_regular_grant(db, store, mixed, granted_at=now - 4 * day)
        make_temporary_grant(db, store, mixed, granted_at=now - hour, valid_until=now + 2 * hour)
        make_temporary_grant(db, store, worker("temp", "최유진"), granted_at=now - 2 * hour,
                             valid_until=now + 2 * hour, title="홀 서빙")
        make_regular_grant(db, store, worker("limited", "박서연"), granted_at=now - 3 * day,
                           valid_until=now + 2 * day)
        make_regular_grant(db, store, worker("revoked", "정하늘"), granted_at=now - 6 * day,
                           revoked_at=now - day)
        make_regular_grant(db, store, worker("expired", "한별"), granted_at=now - 7 * day,
                           valid_until=now - hour)
        db.commit()
        return world, {label: user.id for label, user in names.items()}


def test_list_active_view_and_counts(api, db_engine, now):
    world, ids = _team(db_engine, now)
    login(api, world.owner_id)
    body = api.get(_path(world)).json()
    # Latest grant start first: mixed (now-1h), temp (now-2h), limited (3d), regular (5d).
    assert [item["workerId"] for item in body["items"]] == [ids["mixed"], ids["temp"], ids["limited"], ids["regular"]]
    assert (body["activeCount"], body["expiringCount"], body["endedCount"], body["totalItems"]) == (4, 1, 2, 4)
    assert datetime.fromisoformat(body["asOf"]) == now
    items = {item["workerId"]: item for item in body["items"]}
    mixed = items[ids["mixed"]]
    assert mixed["accessStatus"] == "ACTIVE" and mixed["permissions"] == PERMISSIONS
    assert [g["type"] for g in mixed["accessGrants"]] == ["TEMPORARY", "REGULAR"]
    assert [g["status"] for g in mixed["accessGrants"]] == ["EXPIRING", "ACTIVE"]
    temp = items[ids["temp"]]
    assert temp["accessStatus"] == "EXPIRING" and temp["name"] == "최유진"
    assert temp["accessGrants"][0]["dutyLabel"] == "홀 서빙"  # from the confirmed job posting
    assert items[ids["regular"]]["accessGrants"][0]["dutyLabel"] is None
    assert items[ids["limited"]]["accessStatus"] == "ACTIVE"
    for item in body["items"]:
        assert set(item) == {"workerId", "name", "storeId", "accessStatus", "accessGrants", "permissions", "asOf"}
        assert "example.com" not in str(item)


def test_list_ended_and_all(api, db_engine, now):
    world, ids = _team(db_engine, now)
    login(api, world.owner_id)
    ended = api.get(_path(world, "?view=ENDED")).json()
    assert [item["workerId"] for item in ended["items"]] == [ids["revoked"], ids["expired"]]
    assert {item["accessStatus"] for item in ended["items"]} == {"ENDED"}
    revoked = ended["items"][0]["accessGrants"][0]
    assert revoked["status"] == "REVOKED" and revoked["permissions"] == [] and revoked["revokedAt"]
    assert ended["items"][1]["accessGrants"][0]["status"] == "EXPIRED"
    assert ended["items"][0]["permissions"] == []
    every = api.get(_path(world, "?view=ALL&size=4")).json()
    assert every["totalItems"] == 6 and every["totalPages"] == 2 and len(every["items"]) == 4
    rest = api.get(_path(world, "?view=ALL&page=1&size=4")).json()
    assert [item["workerId"] for item in rest["items"]] == [ids["revoked"], ids["expired"]]
    assert api.get(_path(world, "?view=ALL&page=5")).json()["items"] == []


def test_list_empty_and_validation(api, db_engine, now):
    world = make_world(db_engine)
    login(api, world.owner_id)
    body = api.get(_path(world)).json()
    assert (body["items"], body["activeCount"], body["expiringCount"], body["endedCount"]) == ([], 0, 0, 0)
    for query in ("?view=EXPIRING", "?view=active", "?size=0", "?page=x"):
        assert api.get(_path(world, query)).status_code == 422


def test_detail(api, db_engine, now):
    world, ids = _team(db_engine, now)
    login(api, world.owner_id)
    response = api.get(_path(world, f"/{ids['mixed'].upper()}"))
    assert response.status_code == 200
    assert response.json()["workerId"] == ids["mixed"] and len(response.json()["accessGrants"]) == 2
    ended = api.get(_path(world, f"/{ids['expired']}")).json()
    assert ended["accessStatus"] == "ENDED"


def test_detail_hides_workers_without_history_here(api, db_engine, now):
    world, _ = _team(db_engine, now)
    other = make_world(db_engine, worker_email="other@example.com")
    with Session(db_engine) as db:
        make_regular_grant(db, db.get(Store, other.store_id), db.get(User, other.worker_id),
                           granted_at=now - timedelta(days=1))
        db.commit()
    login(api, world.owner_id)
    for worker_id in (other.worker_id, world.worker_id, world.owner_id, MISSING):
        response = api.get(_path(world, f"/{worker_id}"))
        assert response.status_code == 404 and response.json()["code"] == "STORE_WORKER_NOT_FOUND"
        assert response.json()["message"] == "해당 매장의 근무자를 찾을 수 없습니다."
    assert api.get(_path(world, "/bad")).status_code == 422
    # The other store's worker detail through that store is still the other owner's: 404 store.
    response = api.get(f"/api/stores/{other.store_id}/workers/{other.worker_id}")
    assert response.json()["code"] == "STORE_NOT_FOUND"


@pytest.mark.parametrize("path_suffix", ["", f"/{MISSING}"])
def test_reads_require_approved_owned_store(api, db_engine, path_suffix):
    pending = make_world(db_engine, approved=False)
    world = make_world(db_engine, worker_email="w@example.com")
    login(api, pending.owner_id)
    assert api.get(_path(pending, path_suffix)).json()["code"] == "STORE_APPROVAL_REQUIRED"
    assert api.get(_path(world, path_suffix)).json()["code"] == "STORE_NOT_FOUND"
    login(api, world.worker_id)
    assert api.get(_path(world, path_suffix)).json()["code"] == "FORBIDDEN"
    api.cookies.clear()
    assert api.get(_path(world, path_suffix)).status_code == 401


# --- DELETE /api/stores/{storeId}/workers/{workerId}/access -------------------------------------

def _revoke(api, world, worker_id):
    session = login(api, world.owner_id)
    return api.delete(_path(world, f"/{worker_id}/access"), headers=session.headers())


def _grants(db_engine, worker_id):
    with Session(db_engine) as db:
        return db.scalars(select(StoreAccessGrant).where(StoreAccessGrant.worker_id == worker_id)
                          .order_by(StoreAccessGrant.granted_at)).all()


def test_revoke_ends_every_open_grant_and_keeps_history(api, db_engine, now):
    world, ids = _team(db_engine, now)
    response = _revoke(api, world, ids["mixed"])
    assert response.status_code == 204 and response.content == b""
    assert [g.revoked_at for g in _grants(db_engine, ids["mixed"])] == [now, now]
    login(api, world.owner_id)
    detail = api.get(_path(world, f"/{ids['mixed']}")).json()
    assert detail["accessStatus"] == "ENDED" and {g["status"] for g in detail["accessGrants"]} == {"REVOKED"}
    # Repeating keeps the first revokedAt.
    later = now + timedelta(hours=3)
    store_workers.utcnow = lambda: later  # undone by the `now` fixture's monkeypatch
    assert _revoke(api, world, ids["mixed"]).status_code == 204
    assert [g.revoked_at for g in _grants(db_engine, ids["mixed"])] == [now, now]


def test_revoke_keeps_expired_history_untouched(api, db_engine, now):
    world, ids = _team(db_engine, now)
    assert _revoke(api, world, ids["expired"]).status_code == 204
    [grant] = _grants(db_engine, ids["expired"])
    assert grant.revoked_at is None and grant.valid_until < now
    assert _revoke(api, world, ids["revoked"]).status_code == 204
    [grant] = _grants(db_engine, ids["revoked"])
    assert grant.revoked_at == now - timedelta(days=1)


def test_revoke_only_this_store_and_worker(api, db_engine, now):
    world, ids = _team(db_engine, now)
    other = make_world(db_engine, worker_email="x@example.com")
    with Session(db_engine) as db:
        make_regular_grant(db, db.get(Store, other.store_id), db.get(User, ids["regular"]),
                           granted_at=now - timedelta(days=1))
        db.commit()
    assert _revoke(api, world, ids["regular"]).status_code == 204
    grants = _grants(db_engine, ids["regular"])
    assert {g.store_id: g.revoked_at for g in grants} == {world.store_id: now, other.store_id: None}
    assert all(g.revoked_at is None for g in _grants(db_engine, ids["limited"]))


def test_revoke_cancels_pending_invitations_and_unsent_mail(api, db_engine, now, monkeypatch):
    from app import invitation_mail

    monkeypatch.setenv(invitation_mail.BACKEND_ENV, "disabled")
    world = make_world(db_engine)
    with Session(db_engine) as db:
        make_regular_grant(db, db.get(Store, world.store_id), db.get(User, world.worker_id),
                           granted_at=now - timedelta(days=2), valid_until=now + timedelta(hours=1))
        db.commit()
    # The grant ends within the hour, so the owner already re-invited the worker.
    owner = login(api, world.owner_id)
    created = api.post(f"/api/stores/{world.store_id}/invitations", json={"email": "jisu@example.com"},
                       headers=owner.headers(new_key()))
    assert created.status_code == 409  # still has valid access: re-invite is refused
    with Session(db_engine) as db:
        db.execute(StoreAccessGrant.__table__.update().values(valid_until=now - timedelta(minutes=1)))
        db.commit()
    # Access ended by time, a new invitation is pending, and an older pending one too.
    pending_id = api.post(f"/api/stores/{world.store_id}/invitations", json={"email": WORKER_EMAIL.upper()},
                          headers=owner.headers(new_key())).json()["invitation"]["id"]
    other_email_id, _ = seed_invitation(db_engine, world.store_id, email="someone@example.com")
    assert _revoke(api, world, world.worker_id).status_code == 204
    with Session(db_engine) as db:
        assert db.get(StoreInvitation, pending_id).canceled_at == now
        assert db.get(StoreInvitation, other_email_id).canceled_at is None
        [outbox] = db.scalars(select(InvitationMailOutbox)).all()
        assert outbox.status == "DISCARDED" and outbox.payload is None


def test_revoked_worker_cannot_regain_access_with_an_old_link(api, db_engine, now):
    world = make_world(db_engine)
    invitation_id, token = seed_invitation(db_engine, world.store_id)
    worker = login(api, world.worker_id)
    assert api.post("/api/store-invitations/accept", json={"token": token}, headers=worker.headers()).status_code == 200
    assert _revoke(api, world, world.worker_id).status_code == 204
    worker = login(api, world.worker_id)
    response = api.post("/api/store-invitations/accept", json={"token": token}, headers=worker.headers())
    assert response.status_code == 409
    with Session(db_engine) as db:
        assert db.get(StoreInvitation, invitation_id).accepted_at is not None  # ACCEPTED history kept
    # A new invitation and a new acceptance create a new grant; the old one stays revoked.
    _, new_token = seed_invitation(db_engine, world.store_id)
    worker = login(api, world.worker_id)
    assert api.post("/api/store-invitations/accept", json={"token": new_token}, headers=worker.headers()).status_code == 200
    statuses = sorted(g.revoked_at is None for g in _grants(db_engine, world.worker_id))
    assert statuses == [False, True]


def test_revoke_errors(api, db_engine, now):
    world, ids = _team(db_engine, now)
    for target in (world.worker_id, MISSING, world.owner_id):
        response = _revoke(api, world, target)
        assert response.status_code == 404 and response.json()["code"] == "STORE_WORKER_NOT_FOUND"
    assert _revoke(api, world, "bad").status_code == 422
    pending = make_world(db_engine, approved=False, worker_email="p@example.com")
    assert _revoke(api, pending, ids["regular"]).json()["code"] == "STORE_APPROVAL_REQUIRED"
    session = login(api, pending.owner_id)
    response = api.delete(_path(world, f"/{ids['regular']}/access"), headers=session.headers())
    assert response.json()["code"] == "STORE_NOT_FOUND"
    login(api, world.owner_id)
    response = api.delete(_path(world, f"/{ids['regular']}/access"), headers={"Origin": "http://frontend.test"})
    assert response.status_code == 403 and response.json()["code"] == "CSRF_INVALID"
    worker = login(api, ids["regular"])
    assert api.delete(_path(world, f"/{ids['regular']}/access"), headers=worker.headers()).json()["code"] == "FORBIDDEN"
    assert all(g.revoked_at is None for g in _grants(db_engine, ids["regular"]))


@pytest.mark.mysql
@pytest.mark.parametrize("db_engine", ["mysql"], indirect=True)
def test_revoke_and_accept_race(db_engine, monkeypatch):
    """Ending access and accepting a pending invitation for the same worker are serialized."""
    from fastapi.testclient import TestClient

    from app.auth import SESSION_COOKIE_NAME
    from app.main import app
    from tests.api_contract import ORIGIN

    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("ALLOWED_ORIGINS", ORIGIN)
    world = make_world(db_engine)
    with Session(db_engine) as db:
        # Access history whose period already ended: revocable target, and acceptance is allowed.
        make_regular_grant(db, db.get(Store, world.store_id), db.get(User, world.worker_id),
                           granted_at=utcnow() - timedelta(days=2), valid_until=utcnow() - timedelta(days=1))
        db.commit()
    for _ in range(4):
        invitation_id, token = seed_invitation(db_engine, world.store_id)
        with TestClient(app) as probe:
            worker = login(probe, world.worker_id)
            owner = login(probe, world.owner_id)
        clients = []
        for session in (worker, owner):
            client = TestClient(app, raise_server_exceptions=False)
            client.__enter__()
            client.cookies.set(SESSION_COOKIE_NAME, session.token)
            clients.append(client)
        barrier = threading.Barrier(2)
        results = {}

        def accept(client=clients[0], token=token, session=worker, barrier=barrier, results=results):
            barrier.wait(5)
            results["accept"] = client.post("/api/store-invitations/accept", json={"token": token},
                                            headers=session.headers())

        def revoke(client=clients[1], session=owner, barrier=barrier, results=results):
            barrier.wait(5)
            results["revoke"] = client.delete(_path(world, f"/{world.worker_id}/access"), headers=session.headers())

        threads = [threading.Thread(target=accept), threading.Thread(target=revoke)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)
        assert results["revoke"].status_code == 204
        now = utcnow()
        with Session(db_engine) as db:
            invitation = db.get(StoreInvitation, invitation_id)
            live = db.scalars(select(StoreAccessGrant).where(
                StoreAccessGrant.worker_id == world.worker_id, StoreAccessGrant.revoked_at.is_(None),
                (StoreAccessGrant.valid_until.is_(None)) | (StoreAccessGrant.valid_until > now),
            )).all()
        if results["accept"].status_code == 200:
            # Accept won: revoke ran after it and ended the new grant too.
            assert invitation.accepted_at is not None and live == []
        else:
            # Revoke won: the pending invitation was cancelled and accepting failed.
            assert results["accept"].status_code == 409
            assert invitation.canceled_at is not None and live == []


def test_revoke_ends_a_not_yet_started_grant_at_its_start(api, db_engine, now):
    world = make_world(db_engine)
    start = now + timedelta(hours=2)
    with Session(db_engine) as db:
        store, worker = db.get(Store, world.store_id), db.get(User, world.worker_id)
        make_temporary_grant(db, store, worker, granted_at=start, valid_until=start + timedelta(hours=4))
        make_regular_grant(db, store, worker, granted_at=now - timedelta(days=1))
        db.commit()
    assert _revoke(api, world, world.worker_id).status_code == 204
    assert sorted(g.revoked_at for g in _grants(db_engine, world.worker_id)) == [now, start]
