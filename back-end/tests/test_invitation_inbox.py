import threading
from datetime import datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import invitation_responses
from app.db import utcnow
from app.db.models import Store, StoreAccessGrant, StoreInvitation, User
from tests.api_contract import login
from tests.factories import make_regular_grant, make_temporary_grant
from tests.invitation_helpers import (
    MISSING,
    WORKER_EMAIL,
    configure_mail,
    make_world,
    new_key,
    seed_invitation,
)

INBOX = "/api/users/me/store-invitations"


@pytest.fixture(autouse=True)
def mail_env(monkeypatch):
    return configure_mail(monkeypatch)


def _respond(api, session, invitation_id, decision, key=None):
    return api.post(f"{INBOX}/{invitation_id}/response", json={"decision": decision},
                    headers=session.headers(key or new_key()))


def _grant_count(db_engine):
    with Session(db_engine) as db:
        return db.scalar(select(func.count()).select_from(StoreAccessGrant))


def test_list_own_invitations_by_verified_email(api, db_engine):
    world = make_world(db_engine, worker_email="JiSu@Example.com")
    now = utcnow()
    pending, _ = seed_invitation(db_engine, world.store_id, created_at=now - timedelta(hours=1),
                                 access_expires_at=now + timedelta(days=3))
    older, _ = seed_invitation(db_engine, world.store_id, created_at=now - timedelta(hours=2))
    expired, _ = seed_invitation(db_engine, world.store_id, created_at=now - timedelta(days=8))
    declined, _ = seed_invitation(db_engine, world.store_id, created_at=now - timedelta(days=9),
                                  declined_by_worker_id=world.worker_id, declined_at=now - timedelta(days=9))
    seed_invitation(db_engine, world.store_id, email="jisu+x@example.com")  # an alias is someone else
    login(api, world.worker_id)
    body = api.get(INBOX).json()
    assert [item["id"] for item in body["items"]] == [pending, older]
    assert body["totalItems"] == 2 and "totalPages" not in body
    item = body["items"][0]
    assert item["status"] == "PENDING" and item["store"]["id"] == world.store_id
    assert item["store"]["neighborhood"] == "월계1동"
    assert datetime.fromisoformat(item["accessExpiresAt"]) == now + timedelta(days=3)
    assert set(item) == {"id", "store", "status", "expiresAt", "accessExpiresAt", "createdAt"}
    everything = api.get(f"{INBOX}?filter=ALL").json()
    assert [(i["id"], i["status"]) for i in everything["items"]] == [
        (pending, "PENDING"), (older, "PENDING"), (expired, "EXPIRED"), (declined, "DECLINED"),
    ]
    page = api.get(f"{INBOX}?filter=ALL&page=1&size=3").json()
    assert [i["id"] for i in page["items"]] == [declined] and page["totalItems"] == 4
    for query in ("filter=OPEN", "size=0", "page=-1"):
        assert api.get(f"{INBOX}?{query}").status_code == 422


def test_unverified_email_sees_nothing(api, db_engine):
    world = make_world(db_engine)
    invitation_id, _ = seed_invitation(db_engine, world.store_id)
    with Session(db_engine) as db:
        db.get(User, world.worker_id).email_verified = False
        db.commit()
    login(api, world.worker_id)
    assert api.get(INBOX).json()["items"] == []
    assert api.get(f"{INBOX}/{invitation_id}").status_code == 404


def test_detail_and_other_peoples_invitations(api, db_engine):
    world = make_world(db_engine)
    mine, _ = seed_invitation(db_engine, world.store_id)
    theirs, _ = seed_invitation(db_engine, world.store_id, email="someone@example.com")
    login(api, world.worker_id)
    detail = api.get(f"{INBOX}/{mine.upper()}")
    assert detail.status_code == 200 and detail.json()["id"] == mine
    for target in (theirs, MISSING):
        response = api.get(f"{INBOX}/{target}")
        assert response.status_code == 404 and response.json()["code"] == "RESOURCE_NOT_FOUND"
        response = _respond(api, login(api, world.worker_id), target, "ACCEPT")
        assert response.status_code == 404 and response.json()["code"] == "RESOURCE_NOT_FOUND"
    assert api.get(f"{INBOX}/bad").status_code == 422
    with Session(db_engine) as db:
        assert db.get(StoreInvitation, theirs).accepted_at is None


def test_history_stays_visible(api, db_engine):
    world = make_world(db_engine)
    now = utcnow()
    cancelled, _ = seed_invitation(db_engine, world.store_id, canceled_at=now - timedelta(minutes=1))
    login(api, world.worker_id)
    assert api.get(f"{INBOX}/{cancelled}").json()["status"] == "CANCELLED"


def test_accept_from_inbox_matches_the_token_api(api, db_engine):
    world = make_world(db_engine)
    invitation_id, token = seed_invitation(db_engine, world.store_id)
    session = login(api, world.worker_id)
    key = new_key()
    response = _respond(api, session, invitation_id, "ACCEPT", key)
    assert response.status_code == 200
    body = response.json()
    assert (body["invitationId"], body["storeId"], body["workerId"]) == (invitation_id, world.store_id, world.worker_id)
    assert body["accessGrant"]["type"] == "REGULAR" and body["accessGrant"]["status"] == "ACTIVE"
    # Same transition as the e-mail link: the token now sees an accepted invitation.
    via_token = api.post("/api/store-invitations/accept", json={"token": token}, headers=session.headers())
    assert via_token.status_code == 200 and via_token.json() == body
    replay = _respond(api, session, invitation_id, "ACCEPT", key)
    assert replay.json() == body and replay.headers["Idempotent-Replayed"] == "true"
    reused = _respond(api, session, invitation_id, "DECLINE", key)
    assert reused.status_code == 409 and reused.json()["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert _grant_count(db_engine) == 1
    assert api.get(f"{INBOX}/{invitation_id}").json()["status"] == "ACCEPTED"


def test_decline_from_inbox(api, db_engine):
    world = make_world(db_engine)
    invitation_id, token = seed_invitation(db_engine, world.store_id)
    session = login(api, world.worker_id)
    first = _respond(api, session, invitation_id, "DECLINE")
    assert first.status_code == 200 and first.json()["status"] == "DECLINED"
    again = _respond(api, session, invitation_id, "DECLINE")  # new key: same first declinedAt
    assert again.json() == first.json()
    via_token = api.post("/api/store-invitations/decline", json={"token": token}, headers=session.headers())
    assert via_token.json() == first.json()
    assert _grant_count(db_engine) == 0
    conflict = _respond(api, session, invitation_id, "ACCEPT")
    assert conflict.status_code == 409 and conflict.json()["code"] == "INVITATION_NOT_PENDING"


@pytest.mark.parametrize("state,code", [
    ("cancelled", "INVITATION_NOT_PENDING"),
    ("link_expired", "INVITATION_EXPIRED"),
    ("access_ended", "ACCESS_PERIOD_ENDED"),
])
@pytest.mark.parametrize("decision", ["ACCEPT", "DECLINE"])
def test_inbox_maps_errors_to_its_contract(api, db_engine, state, code, decision):
    world = make_world(db_engine)
    now = utcnow()
    kwargs = {
        "cancelled": {"canceled_at": now - timedelta(minutes=1)},
        "link_expired": {"created_at": now - timedelta(days=7)},
        "access_ended": {"access_expires_at": now},
    }[state]
    invitation_id, _ = seed_invitation(db_engine, world.store_id, **kwargs)
    session = login(api, world.worker_id)
    response = _respond(api, session, invitation_id, decision)
    assert (response.status_code, response.json()["code"]) == (409, code)


def test_existing_regular_access_is_returned_and_the_invitation_stays_pending(api, db_engine):
    """Inbox spec: an existing valid REGULAR grant is returned, not duplicated (token API: 409)."""
    world = make_world(db_engine)
    with Session(db_engine) as db:
        existing = make_regular_grant(db, db.get(Store, world.store_id), db.get(User, world.worker_id),
                                      granted_at=utcnow() - timedelta(days=1))
        db.commit()
        existing_id = existing.id
    invitation_id, token = seed_invitation(db_engine, world.store_id)
    session = login(api, world.worker_id)
    key = new_key()
    response = _respond(api, session, invitation_id, "ACCEPT", key)
    assert response.status_code == 200
    body = response.json()
    assert (body["invitationId"], body["storeId"], body["workerId"]) == (invitation_id, world.store_id, world.worker_id)
    assert body["accessGrant"]["id"] == existing_id and body["accessGrant"]["status"] == "ACTIVE"
    assert _grant_count(db_engine) == 1
    with Session(db_engine) as db:
        invitation = db.get(StoreInvitation, invitation_id)
        assert (invitation.accepted_at, invitation.declined_at, invitation.canceled_at) == (None, None, None)
    assert api.get(f"{INBOX}/{invitation_id}").json()["status"] == "PENDING"
    replay = _respond(api, session, invitation_id, "ACCEPT", key)
    assert replay.json() == body and replay.headers["Idempotent-Replayed"] == "true"
    # The e-mail link keeps its own contract for the same situation.
    via_token = api.post("/api/store-invitations/accept", json={"token": token}, headers=session.headers())
    assert via_token.status_code == 409 and via_token.json()["code"] == "WORKER_ALREADY_HAS_ACCESS"
    # Once the existing access ends, the still-pending invitation can be accepted for real.
    with Session(db_engine) as db:
        db.get(StoreAccessGrant, existing_id).revoked_at = utcnow()
        db.commit()
    accepted = _respond(api, session, invitation_id, "ACCEPT")
    assert accepted.status_code == 200 and accepted.json()["accessGrant"]["id"] != existing_id
    assert _grant_count(db_engine) == 2
    assert api.get(f"{INBOX}/{invitation_id}").json()["status"] == "ACCEPTED"


def test_existing_regular_access_does_not_block_declining(api, db_engine):
    world = make_world(db_engine)
    with Session(db_engine) as db:
        make_regular_grant(db, db.get(Store, world.store_id), db.get(User, world.worker_id),
                           granted_at=utcnow() - timedelta(days=1))
        db.commit()
    invitation_id, _ = seed_invitation(db_engine, world.store_id)
    response = _respond(api, login(api, world.worker_id), invitation_id, "DECLINE")
    assert response.status_code == 200 and response.json()["status"] == "DECLINED"


def test_store_must_operate_for_inbox_responses(api, db_engine):
    world = make_world(db_engine)
    invitation_id, _ = seed_invitation(db_engine, world.store_id)
    session = login(api, world.worker_id)
    with Session(db_engine) as db:
        store = db.get(Store, world.store_id)
        store.approval_status, store.approved_at = "PENDING", None
        db.commit()
    response = _respond(api, session, invitation_id, "DECLINE")
    assert response.status_code == 403 and response.json()["code"] == "STORE_APPROVAL_REQUIRED"



def test_inbox_accept_with_temporary_shift_access_creates_regular_access(api, db_engine):
    world = make_world(db_engine)
    with Session(db_engine) as db:
        make_temporary_grant(db, db.get(Store, world.store_id), db.get(User, world.worker_id),
                             granted_at=utcnow() - timedelta(hours=1), valid_until=utcnow() + timedelta(hours=3))
        db.commit()
    invitation_id, _ = seed_invitation(db_engine, world.store_id)
    response = _respond(api, login(api, world.worker_id), invitation_id, "ACCEPT")
    assert response.status_code == 200, response.text
    assert response.json()["accessGrant"]["type"] == "REGULAR"

def test_failed_response_releases_the_key(api, db_engine, monkeypatch):
    world = make_world(db_engine)
    invitation_id, _ = seed_invitation(db_engine, world.store_id)
    session = login(api, world.worker_id)
    key = new_key()
    real = invitation_responses._accept

    def broken(*args, **kwargs):
        raise RuntimeError("injected")

    monkeypatch.setattr(invitation_responses, "_accept", broken)
    assert _respond(api, session, invitation_id, "ACCEPT", key).status_code == 500
    with Session(db_engine) as db:
        assert db.get(StoreInvitation, invitation_id).accepted_at is None
    monkeypatch.setattr(invitation_responses, "_accept", real)
    assert _respond(api, session, invitation_id, "ACCEPT", key).status_code == 200


def test_replay_rechecks_the_recipient(api, db_engine):
    world = make_world(db_engine)
    invitation_id, _ = seed_invitation(db_engine, world.store_id)
    session = login(api, world.worker_id)
    key = new_key()
    assert _respond(api, session, invitation_id, "DECLINE", key).status_code == 200
    with Session(db_engine) as db:
        db.get(User, world.worker_id).google_email = "changed@example.com"
        db.commit()
    assert _respond(api, session, invitation_id, "DECLINE", key).status_code == 404


@pytest.mark.parametrize("body", [{}, {"decision": "accept"}, {"decision": "EXPIRED"},
                                  {"decision": "ACCEPT", "email": WORKER_EMAIL}])
def test_response_validation(api, db_engine, body):
    world = make_world(db_engine)
    invitation_id, _ = seed_invitation(db_engine, world.store_id)
    session = login(api, world.worker_id)
    response = api.post(f"{INBOX}/{invitation_id}/response", json=body, headers=session.headers(new_key()))
    assert response.status_code == 422


def test_response_requires_key_csrf_and_worker(api, db_engine):
    world = make_world(db_engine)
    invitation_id, _ = seed_invitation(db_engine, world.store_id)
    path = f"{INBOX}/{invitation_id}/response"
    assert api.post(path, json={"decision": "ACCEPT"}, headers={"Origin": "http://frontend.test"}).status_code == 401
    session = login(api, world.worker_id)
    assert api.post(path, json={"decision": "ACCEPT"}, headers=session.headers()).status_code == 422
    bad = session.headers(new_key())
    bad["Origin"] = "http://evil.test"
    assert api.post(path, json={"decision": "ACCEPT"}, headers=bad).json()["code"] == "CSRF_INVALID"
    owner = login(api, world.owner_id)
    assert api.post(path, json={"decision": "ACCEPT"}, headers=owner.headers(new_key())).json()["code"] == "FORBIDDEN"
    assert api.get(INBOX).json()["code"] == "FORBIDDEN"
    api.cookies.clear()
    assert api.get(INBOX).status_code == 401
    assert _grant_count(db_engine) == 0


@pytest.mark.mysql
@pytest.mark.parametrize("db_engine", ["mysql"], indirect=True)
def test_inbox_and_link_race_accept_once(db_engine, monkeypatch):
    from fastapi.testclient import TestClient

    from app.auth import SESSION_COOKIE_NAME
    from app.main import app
    from tests.api_contract import ORIGIN, validate_response

    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("ALLOWED_ORIGINS", ORIGIN)
    world = make_world(db_engine)
    invitation_id, token = seed_invitation(db_engine, world.store_id)
    with TestClient(app) as probe:
        session = login(probe, world.worker_id)
    clients = []
    for _ in range(4):
        client = TestClient(app, raise_server_exceptions=False)
        client.__enter__()
        client.cookies.set(SESSION_COOKIE_NAME, session.token)
        clients.append(client)
    barrier = threading.Barrier(4)
    results = [None] * 4

    def run(index):
        barrier.wait(5)
        client = clients[index]
        if index % 2:
            response = client.post("/api/store-invitations/accept", json={"token": token}, headers=session.headers())
        else:
            response = client.post(f"{INBOX}/{invitation_id}/response", json={"decision": "ACCEPT"},
                                   headers=session.headers(new_key()))
        validate_response(response)
        results[index] = response

    threads = [threading.Thread(target=run, args=(i,)) for i in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    assert [r.status_code for r in results] == [200] * 4
    assert len({r.json()["accessGrant"]["id"] for r in results}) == 1
    assert _grant_count(db_engine) == 1
