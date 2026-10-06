import threading
from datetime import datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import invitation_responses
from app.db import utcnow
from app.db.models import Store, StoreAccessGrant, StoreInvitation, User
from tests.api_contract import login
from tests.factories import make_regular_grant, make_temporary_grant, make_worker
from tests.invitation_helpers import (
    WORKER_EMAIL,
    configure_mail,
    make_world,
    new_key,
    seed_invitation,
    token_of,
)

PREVIEW = "/api/store-invitations/preview"
ACCEPT = "/api/store-invitations/accept"
DECLINE = "/api/store-invitations/decline"


@pytest.fixture(autouse=True)
def mail_env(monkeypatch):
    return configure_mail(monkeypatch)


def _as_worker(api, user_id):
    return login(api, user_id)


def _post(api, session, path, token):
    headers = session.headers() if path != PREVIEW else {}
    return api.post(path, json={"token": token}, headers=headers)


def _grants(db_engine):
    with Session(db_engine) as db:
        return db.scalars(select(StoreAccessGrant)).all()


def _invitation(db_engine, invitation_id):
    with Session(db_engine) as db:
        return db.get(StoreInvitation, invitation_id)


# --- preview ----------------------------------------------------------------------------------

def test_preview_shows_store_and_periods_without_changing_anything(api, db_engine):
    world = make_world(db_engine)
    access_end = utcnow() + timedelta(days=20)
    invitation_id, token = seed_invitation(db_engine, world.store_id, access_expires_at=access_end)
    before = _invitation(db_engine, invitation_id)
    session = _as_worker(api, world.worker_id)
    response = _post(api, session, PREVIEW, token)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["invitationId"] == invitation_id and body["status"] == "PENDING"
    assert body["store"] == {"id": world.store_id, "name": "월계 카페"}
    assert body["accessType"] == "REGULAR"
    assert datetime.fromisoformat(body["accessExpiresAt"]) == access_end
    assert datetime.fromisoformat(body["expiresAt"]) == before.expires_at
    assert token not in response.text
    after = _invitation(db_engine, invitation_id)
    assert (after.token_hash, after.last_sent_at, after.accepted_at) == (before.token_hash, before.last_sent_at, None)


def test_full_flow_from_the_mailed_link(api, db_engine, mail_env):
    world = make_world(db_engine)
    owner = login(api, world.owner_id)
    created = api.post(f"/api/stores/{world.store_id}/invitations", json={"email": "JISU@example.com"},
                       headers=owner.headers(new_key()))
    assert created.status_code == 201
    token = token_of(mail_env.messages[0])
    session = _as_worker(api, world.worker_id)
    assert _post(api, session, PREVIEW, token).status_code == 200
    accepted = _post(api, session, ACCEPT, token)
    assert accepted.status_code == 200
    login(api, world.owner_id)
    listed = api.get(f"/api/stores/{world.store_id}/invitations?view=PAST").json()
    assert listed["items"][0]["status"] == "ACCEPTED"
    assert listed["items"][0]["acceptedBy"]["workerId"] == world.worker_id
    summary = api.get(f"/api/stores/{world.store_id}/management-summary").json()
    assert (summary["pendingInvitationCount"], summary["activeWorkerCount"]) == (0, 1)


def test_email_matching_ignores_case_and_spaces(api, db_engine):
    world = make_world(db_engine, worker_email="JiSu@Example.com")
    _, token = seed_invitation(db_engine, world.store_id, email=WORKER_EMAIL)
    session = _as_worker(api, world.worker_id)
    assert _post(api, session, PREVIEW, token).status_code == 200


@pytest.mark.parametrize("path", [PREVIEW, ACCEPT, DECLINE])
def test_email_mismatch_reveals_nothing(api, db_engine, path):
    world = make_world(db_engine)
    invitation_id, token = seed_invitation(db_engine, world.store_id, email="jisu+other@example.com")
    session = _as_worker(api, world.worker_id)
    response = _post(api, session, path, token)
    assert response.status_code == 403 and response.json()["code"] == "INVITATION_EMAIL_MISMATCH"
    assert response.json()["message"] == "초대 받은 Google 계정으로 로그인해 주세요."
    for secret in (invitation_id, world.store_id, "월계 카페"):
        assert secret not in response.text
    assert _invitation(db_engine, invitation_id).accepted_at is None
    assert _invitation(db_engine, invitation_id).declined_at is None


@pytest.mark.parametrize("path", [PREVIEW, ACCEPT, DECLINE])
def test_unverified_google_email_is_a_mismatch(api, db_engine, path):
    world = make_world(db_engine)
    _, token = seed_invitation(db_engine, world.store_id)
    with Session(db_engine) as db:
        db.get(User, world.worker_id).email_verified = False
        db.commit()
    session = _as_worker(api, world.worker_id)
    assert _post(api, session, path, token).json()["code"] == "INVITATION_EMAIL_MISMATCH"


@pytest.mark.parametrize("path", [PREVIEW, ACCEPT, DECLINE])
def test_unknown_token(api, db_engine, path):
    world = make_world(db_engine)
    session = _as_worker(api, world.worker_id)
    response = _post(api, session, path, "A" * 43)
    assert response.status_code == 404 and response.json()["code"] == "INVITATION_NOT_FOUND"


@pytest.mark.parametrize("path", [PREVIEW, ACCEPT, DECLINE])
@pytest.mark.parametrize("token", ["A" * 42, "A" * 513, "A" * 42 + "=", "A" * 42 + "+", "A" * 42 + " ", 123, None])
def test_token_format(api, db_engine, path, token):
    world = make_world(db_engine)
    session = _as_worker(api, world.worker_id)
    response = api.post(path, json={"token": token}, headers=session.headers() if path != PREVIEW else {})
    assert response.status_code == 422
    if isinstance(token, str):
        assert token not in response.text


@pytest.mark.parametrize("path", [PREVIEW, ACCEPT, DECLINE])
def test_extra_fields_and_missing_body(api, db_engine, path):
    world = make_world(db_engine)
    _, token = seed_invitation(db_engine, world.store_id)
    session = _as_worker(api, world.worker_id)
    headers = session.headers() if path != PREVIEW else {}
    assert api.post(path, json={"token": token, "workerId": world.worker_id}, headers=headers).status_code == 422
    assert api.post(path, json={}, headers=headers).status_code == 422
    malformed = api.post(path, content=b"{", headers={**headers, "Content-Type": "application/json"})
    assert malformed.status_code == 400


@pytest.mark.parametrize("path", [PREVIEW, ACCEPT, DECLINE])
@pytest.mark.parametrize("change", ["pending_store", "suspended_owner"])
def test_store_must_still_operate(api, db_engine, path, change):
    world = make_world(db_engine)
    _, token = seed_invitation(db_engine, world.store_id)
    with Session(db_engine) as db:
        if change == "pending_store":
            store = db.get(Store, world.store_id)
            store.approval_status, store.approved_at = "PENDING", None
        else:
            db.get(User, world.owner_id).status = "SUSPENDED"
        db.commit()
    session = _as_worker(api, world.worker_id)
    response = _post(api, session, path, token)
    assert response.status_code == 403 and response.json()["code"] == "STORE_APPROVAL_REQUIRED"
    assert _grants(db_engine) == []


@pytest.mark.parametrize("path", [PREVIEW, ACCEPT, DECLINE])
def test_role_session_and_csrf(api, db_engine, path):
    world = make_world(db_engine)
    _, token = seed_invitation(db_engine, world.store_id)
    response = api.post(path, json={"token": token}, headers={"Origin": "http://frontend.test"})
    assert response.status_code == 401
    owner = login(api, world.owner_id)
    response = api.post(path, json={"token": token}, headers=owner.headers())
    assert response.status_code == 403 and response.json()["code"] == "FORBIDDEN"
    if path != PREVIEW:
        worker = _as_worker(api, world.worker_id)
        headers = worker.headers()
        headers["X-CSRF-Token"] = "bad"
        response = api.post(path, json={"token": token}, headers=headers)
        assert response.status_code == 403 and response.json()["code"] == "CSRF_INVALID"
        assert _grants(db_engine) == []


def _seed_state(db_engine, world, state, other_worker_id=None):
    now = utcnow()
    actor = other_worker_id or world.worker_id
    kwargs = {
        "pending": {},
        "accepted": {"accepted_by_worker_id": actor, "accepted_at": now - timedelta(minutes=5)},
        "declined": {"declined_by_worker_id": actor, "declined_at": now - timedelta(minutes=5)},
        "cancelled": {"canceled_at": now - timedelta(minutes=5)},
        "link_expired": {"created_at": now - timedelta(days=7)},
        "access_ended": {"access_expires_at": now - timedelta(microseconds=1)},
    }[state]
    return seed_invitation(db_engine, world.store_id, **kwargs)


@pytest.mark.parametrize("state,status,code", [
    ("accepted", 409, "INVITATION_STATE_CONFLICT"),
    ("declined", 409, "INVITATION_STATE_CONFLICT"),
    ("cancelled", 409, "INVITATION_STATE_CONFLICT"),
    ("link_expired", 410, "INVITATION_EXPIRED"),
    ("access_ended", 410, "ACCESS_PERIOD_ENDED"),
])
def test_preview_only_shows_pending(api, db_engine, state, status, code):
    world = make_world(db_engine)
    _, token = _seed_state(db_engine, world, state)
    session = _as_worker(api, world.worker_id)
    response = _post(api, session, PREVIEW, token)
    assert (response.status_code, response.json()["code"]) == (status, code)


def test_earlier_deadline_decides_the_expiry_code(api, db_engine):
    world = make_world(db_engine)
    now = utcnow()
    _, token = seed_invitation(db_engine, world.store_id, created_at=now - timedelta(days=8),
                               access_expires_at=now - timedelta(days=7, hours=12))
    session = _as_worker(api, world.worker_id)
    assert _post(api, session, PREVIEW, token).json()["code"] == "ACCESS_PERIOD_ENDED"
    _, token = seed_invitation(db_engine, world.store_id, email="x@example.com", created_at=now - timedelta(days=8),
                               access_expires_at=now - timedelta(minutes=1))
    with Session(db_engine) as db:
        db.get(User, world.worker_id).google_email = "x@example.com"
        db.commit()
    assert _post(api, session, PREVIEW, token).json()["code"] == "INVITATION_EXPIRED"


# --- accept -----------------------------------------------------------------------------------

def test_accept_creates_a_regular_grant(api, db_engine, monkeypatch):
    world = make_world(db_engine)
    access_end = utcnow() + timedelta(hours=10)
    invitation_id, token = seed_invitation(db_engine, world.store_id, access_expires_at=access_end)
    session = _as_worker(api, world.worker_id)
    response = _post(api, session, ACCEPT, token)
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["invitationId"], body["storeId"], body["workerId"]) == (invitation_id, world.store_id, world.worker_id)
    grant = body["accessGrant"]
    assert grant["type"] == "REGULAR" and grant["dutyLabel"] is None and grant["revokedAt"] is None
    assert grant["status"] == "EXPIRING"  # ends within 24 hours
    assert grant["permissions"] == ["READ_MANUALS", "READ_CHECKLISTS", "USE_AI_QA"]
    assert datetime.fromisoformat(grant["validUntil"]) == access_end
    invitation = _invitation(db_engine, invitation_id)
    assert invitation.accepted_by_worker_id == world.worker_id
    assert datetime.fromisoformat(grant["startedAt"]) == invitation.accepted_at
    [row] = _grants(db_engine)
    assert (row.invitation_id, row.worker_id, row.store_id, row.valid_until) == (
        invitation_id, world.worker_id, world.store_id, access_end,
    )

    # Repeating keeps the first start and period and creates nothing new.
    later = utcnow() + timedelta(hours=1)
    monkeypatch.setattr(invitation_responses, "utcnow", lambda: later)
    again = _post(api, session, ACCEPT, token)
    assert again.status_code == 200
    assert again.json()["accessGrant"]["id"] == grant["id"]
    assert again.json()["accessGrant"]["startedAt"] == grant["startedAt"]
    assert len(_grants(db_engine)) == 1
    # The token cannot be reused for a decline, and preview reports the completed state.
    assert _post(api, session, DECLINE, token).json()["code"] == "INVITATION_STATE_CONFLICT"
    assert _post(api, session, PREVIEW, token).json()["code"] == "INVITATION_STATE_CONFLICT"


def test_unlimited_access_is_active(api, db_engine):
    world = make_world(db_engine)
    _, token = seed_invitation(db_engine, world.store_id)
    session = _as_worker(api, world.worker_id)
    grant = _post(api, session, ACCEPT, token).json()["accessGrant"]
    assert (grant["status"], grant["validUntil"]) == ("ACTIVE", None)


@pytest.mark.parametrize("state,code", [
    ("declined", "INVITATION_STATE_CONFLICT"), ("cancelled", "INVITATION_STATE_CONFLICT"),
    ("link_expired", "INVITATION_EXPIRED"), ("access_ended", "ACCESS_PERIOD_ENDED"),
])
def test_accept_refusals(api, db_engine, state, code):
    world = make_world(db_engine)
    _, token = _seed_state(db_engine, world, state)
    session = _as_worker(api, world.worker_id)
    response = _post(api, session, ACCEPT, token)
    assert response.json()["code"] == code
    assert _grants(db_engine) == []


def test_accepted_by_another_worker(api, db_engine):
    world = make_world(db_engine)
    with Session(db_engine) as db:
        twin = make_worker(db)
        twin.google_email = WORKER_EMAIL  # a second account with the same address
        db.commit()
        twin_id = twin.id
    _, token = seed_invitation(db_engine, world.store_id)
    assert _post(api, _as_worker(api, twin_id), ACCEPT, token).status_code == 200
    response = _post(api, _as_worker(api, world.worker_id), ACCEPT, token)
    assert response.status_code == 409 and response.json()["message"] == "현재 초대 상태에서는 수락할 수 없습니다."
    assert _post(api, _as_worker(api, world.worker_id), DECLINE, token).status_code == 409


def test_reaccept_after_revocation_or_deadline(api, db_engine, monkeypatch):
    world = make_world(db_engine)
    access_end = utcnow() + timedelta(days=2)
    _, token = seed_invitation(db_engine, world.store_id, access_expires_at=access_end)
    session = _as_worker(api, world.worker_id)
    assert _post(api, session, ACCEPT, token).status_code == 200
    with Session(db_engine) as db:
        db.scalars(select(StoreAccessGrant)).one().revoked_at = utcnow()
        db.commit()
    revoked = _post(api, session, ACCEPT, token)
    assert revoked.status_code == 409 and revoked.json()["code"] == "INVITATION_STATE_CONFLICT"
    later = access_end + timedelta(seconds=1)
    monkeypatch.setattr(invitation_responses, "utcnow", lambda: later)
    assert _post(api, session, ACCEPT, token).json()["code"] == "ACCESS_PERIOD_ENDED"
    assert len(_grants(db_engine)) == 1
    assert _invitation(db_engine, _grants(db_engine)[0].invitation_id).accepted_at is not None


def test_reaccept_after_link_expiry_is_gone(api, db_engine, monkeypatch):
    world = make_world(db_engine)
    invitation_id, token = seed_invitation(db_engine, world.store_id)
    session = _as_worker(api, world.worker_id)
    assert _post(api, session, ACCEPT, token).status_code == 200
    later = _invitation(db_engine, invitation_id).expires_at
    monkeypatch.setattr(invitation_responses, "utcnow", lambda: later)
    assert _post(api, session, ACCEPT, token).json()["code"] == "INVITATION_EXPIRED"



def test_temporary_shift_access_coexists_with_an_accepted_invitation(api, db_engine):
    world = make_world(db_engine)
    with Session(db_engine) as db:
        temporary = make_temporary_grant(db, db.get(Store, world.store_id), db.get(User, world.worker_id),
                                         granted_at=utcnow() - timedelta(hours=1),
                                         valid_until=utcnow() + timedelta(hours=3))
        db.commit()
        temporary_id = temporary.id
    _, token = seed_invitation(db_engine, world.store_id)
    session = _as_worker(api, world.worker_id)
    response = _post(api, session, ACCEPT, token)
    assert response.status_code == 200, response.text
    assert response.json()["accessGrant"]["type"] == "REGULAR"
    access = api.get(f"/api/users/me/stores/{world.store_id}/access").json()["access"]
    grants = {g["id"]: g for g in access["accessGrants"]}
    assert grants[temporary_id]["type"] == "TEMPORARY" and grants[temporary_id]["status"] == "EXPIRING"
    assert sorted(g["type"] for g in grants.values()) == ["REGULAR", "TEMPORARY"]
    assert access["accessStatus"] == "ACTIVE"  # the open-ended regular grant outlasts the shift

def test_worker_with_valid_access_cannot_accept_again(api, db_engine):
    world = make_world(db_engine)
    with Session(db_engine) as db:
        make_regular_grant(db, db.get(Store, world.store_id), db.get(User, world.worker_id),
                           granted_at=utcnow() - timedelta(days=1))
        db.commit()
    invitation_id, token = seed_invitation(db_engine, world.store_id)
    response = _post(api, _as_worker(api, world.worker_id), ACCEPT, token)
    assert response.status_code == 409 and response.json()["code"] == "WORKER_ALREADY_HAS_ACCESS"
    assert _invitation(db_engine, invitation_id).accepted_at is None


def test_replaced_token_is_not_found(api, db_engine, mail_env):
    world = make_world(db_engine)
    owner = login(api, world.owner_id)
    invitation_id = api.post(f"/api/stores/{world.store_id}/invitations", json={"email": WORKER_EMAIL},
                             headers=owner.headers(new_key())).json()["invitation"]["id"]
    old = token_of(mail_env.messages[0])
    api.post(f"/api/stores/{world.store_id}/invitations/{invitation_id}/resend", headers=owner.headers(new_key()))
    new = token_of(mail_env.messages[1])
    session = _as_worker(api, world.worker_id)
    for path in (PREVIEW, ACCEPT, DECLINE):
        assert _post(api, session, path, old).json()["code"] == "INVITATION_NOT_FOUND"
    assert _post(api, session, ACCEPT, new).status_code == 200


def test_cancelled_link_cannot_be_used(api, db_engine):
    world = make_world(db_engine)
    invitation_id, token = seed_invitation(db_engine, world.store_id)
    owner = login(api, world.owner_id)
    assert api.post(f"/api/stores/{world.store_id}/invitations/{invitation_id}/cancel",
                    headers=owner.headers()).status_code == 200
    session = _as_worker(api, world.worker_id)
    assert _post(api, session, ACCEPT, token).json()["code"] == "INVITATION_STATE_CONFLICT"
    assert _grants(db_engine) == []
    owner = login(api, world.owner_id)
    # Cancelling after acceptance is a conflict, and acceptance is not undone.
    other_id, other_token = seed_invitation(db_engine, world.store_id)
    assert _post(api, _as_worker(api, world.worker_id), ACCEPT, other_token).status_code == 200
    owner = login(api, world.owner_id)
    response = api.post(f"/api/stores/{world.store_id}/invitations/{other_id}/cancel", headers=owner.headers())
    assert response.status_code == 409
    assert len(_grants(db_engine)) == 1


# --- decline ----------------------------------------------------------------------------------

def test_decline_and_repeat(api, db_engine, monkeypatch):
    world = make_world(db_engine)
    invitation_id, token = seed_invitation(db_engine, world.store_id)
    session = _as_worker(api, world.worker_id)
    first = _post(api, session, DECLINE, token)
    assert first.status_code == 200
    assert first.json()["invitationId"] == invitation_id and first.json()["status"] == "DECLINED"
    again = _post(api, session, DECLINE, token)
    assert again.status_code == 200 and again.json() == first.json()
    assert _grants(db_engine) == []
    assert _post(api, session, ACCEPT, token).json()["code"] == "INVITATION_STATE_CONFLICT"
    later = _invitation(db_engine, invitation_id).expires_at
    monkeypatch.setattr(invitation_responses, "utcnow", lambda: later)
    assert _post(api, session, DECLINE, token).json()["code"] == "INVITATION_EXPIRED"


@pytest.mark.parametrize("state,code", [
    ("accepted", "INVITATION_STATE_CONFLICT"), ("cancelled", "INVITATION_STATE_CONFLICT"),
    ("link_expired", "INVITATION_EXPIRED"), ("access_ended", "ACCESS_PERIOD_ENDED"),
])
def test_decline_refusals(api, db_engine, state, code):
    world = make_world(db_engine)
    _, token = _seed_state(db_engine, world, state)
    response = _post(api, _as_worker(api, world.worker_id), DECLINE, token)
    assert response.json()["code"] == code


def test_decline_by_another_account_is_not_replayed(api, db_engine):
    world = make_world(db_engine)
    with Session(db_engine) as db:
        twin = make_worker(db)
        twin.google_email = WORKER_EMAIL
        db.commit()
        twin_id = twin.id
    _, token = _seed_state(db_engine, world, "declined", other_worker_id=twin_id)
    assert _post(api, _as_worker(api, world.worker_id), DECLINE, token).status_code == 409


def test_decline_keeps_existing_access(api, db_engine):
    world = make_world(db_engine)
    with Session(db_engine) as db:
        from app.db.models import Store as StoreModel
        from tests.factories import make_store_with_request
        other, _ = make_store_with_request(db, approved_at=utcnow() - timedelta(days=3))
        make_regular_grant(db, db.get(StoreModel, other.id), db.get(User, world.worker_id),
                           granted_at=utcnow() - timedelta(days=1))
        db.commit()
    _, token = seed_invitation(db_engine, world.store_id)
    assert _post(api, _as_worker(api, world.worker_id), DECLINE, token).status_code == 200
    assert [grant.revoked_at for grant in _grants(db_engine)] == [None]


# --- races (MySQL row locks) ------------------------------------------------------------------

def _clients(n, session):
    from fastapi.testclient import TestClient

    from app.auth import SESSION_COOKIE_NAME
    from app.main import app

    clients = [TestClient(app, raise_server_exceptions=False) for _ in range(n)]
    for client in clients:
        client.__enter__()
        client.cookies.set(SESSION_COOKIE_NAME, session.token)
    return clients


def _race(calls):
    barrier = threading.Barrier(len(calls))
    results = [None] * len(calls)

    def run(index, call):
        barrier.wait(5)
        results[index] = call()

    threads = [threading.Thread(target=run, args=item) for item in enumerate(calls)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    return results


@pytest.mark.mysql
@pytest.mark.parametrize("db_engine", ["mysql"], indirect=True)
def test_concurrent_accepts_create_one_grant(db_engine, monkeypatch):
    from tests.api_contract import ORIGIN, validate_response

    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("ALLOWED_ORIGINS", ORIGIN)
    world = make_world(db_engine)
    _, token = seed_invitation(db_engine, world.store_id)
    from fastapi.testclient import TestClient

    from app.main import app
    with TestClient(app) as probe:
        session = login(probe, world.worker_id)
    clients = _clients(5, session)
    responses = _race([lambda c=c: c.post(ACCEPT, json={"token": token}, headers=session.headers()) for c in clients])
    for response in responses:
        validate_response(response)
    assert [r.status_code for r in responses] == [200] * 5
    assert len({r.json()["accessGrant"]["id"] for r in responses}) == 1
    with Session(db_engine) as db:
        assert db.scalar(select(func.count()).select_from(StoreAccessGrant)) == 1


@pytest.mark.mysql
@pytest.mark.parametrize("db_engine", ["mysql"], indirect=True)
def test_accept_cancel_race_has_one_outcome(db_engine, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app
    from tests.api_contract import ORIGIN, validate_response

    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("ALLOWED_ORIGINS", ORIGIN)
    world = make_world(db_engine)
    for _ in range(5):
        invitation_id, token = seed_invitation(db_engine, world.store_id)
        with TestClient(app) as probe:
            worker = login(probe, world.worker_id)
            owner = login(probe, world.owner_id)
        [worker_client] = _clients(1, worker)
        [owner_client] = _clients(1, owner)
        cancel_path = f"/api/stores/{world.store_id}/invitations/{invitation_id}/cancel"
        accept, cancel = _race([
            lambda w=worker_client, t=token, s=worker: w.post(ACCEPT, json={"token": t}, headers=s.headers()),
            lambda o=owner_client, p=cancel_path, s=owner: o.post(p, headers=s.headers()),
        ])
        validate_response(accept)
        validate_response(cancel)
        assert sorted([accept.status_code, cancel.status_code]) == [200, 409]
        invitation = _invitation(db_engine, invitation_id)
        assert (invitation.accepted_at is None) != (invitation.canceled_at is None)
        grants = [g for g in _grants(db_engine) if g.invitation_id == invitation_id]
        assert len(grants) == (1 if accept.status_code == 200 else 0)
        # Reset for the next round: end the access so a new invitation can be accepted.
        with Session(db_engine) as db:
            for grant in db.scalars(select(StoreAccessGrant).where(StoreAccessGrant.revoked_at.is_(None))):
                grant.revoked_at = utcnow()
            db.commit()


@pytest.mark.mysql
@pytest.mark.parametrize("db_engine", ["mysql"], indirect=True)
def test_accept_resend_race_never_accepts_with_a_replaced_token(db_engine, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app
    from tests.api_contract import ORIGIN

    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("ALLOWED_ORIGINS", ORIGIN)
    world = make_world(db_engine)
    invitation_id, token = seed_invitation(db_engine, world.store_id)
    with TestClient(app) as probe:
        worker = login(probe, world.worker_id)
        owner = login(probe, world.owner_id)
    [worker_client] = _clients(1, worker)
    [owner_client] = _clients(1, owner)
    accept, resend = _race([
        lambda: worker_client.post(ACCEPT, json={"token": token}, headers=worker.headers()),
        lambda: owner_client.post(f"/api/stores/{world.store_id}/invitations/{invitation_id}/resend",
                                  headers=owner.headers(new_key())),
    ])
    invitation = _invitation(db_engine, invitation_id)
    if accept.status_code == 200:
        assert resend.status_code == 409 and invitation.accepted_at is not None
    else:
        assert accept.status_code == 404 and resend.status_code == 200
        assert invitation.accepted_at is None and _grants(db_engine) == []
