import threading
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import invitation_mail, invitations
from app.db import utcnow
from app.db.models import InvitationMailOutbox, StoreAccessGrant, StoreInvitation
from app.invitations import hash_invitation_token
from tests.api_contract import login
from tests.factories import make_regular_grant, make_store_with_request, make_temporary_grant
from tests.invitation_helpers import (
    FRONTEND,
    MISSING,
    WORKER_EMAIL,
    configure_mail,
    make_world,
    new_key,
    seed_invitation,
    token_of,
)


@pytest.fixture(autouse=True)
def mail_env(monkeypatch):
    return configure_mail(monkeypatch)


def _path(world, suffix=""):
    return f"/api/stores/{world.store_id}/invitations{suffix}"


def _create(api, world, body=None, key=None):
    session = login(api, world.owner_id)
    return api.post(_path(world), json=body or {"email": WORKER_EMAIL}, headers=session.headers(key or new_key()))


def _rows(db_engine, model):
    with Session(db_engine) as db:
        return db.scalars(select(model)).all()


# --- POST /api/stores/{storeId}/invitations --------------------------------------------------------

def test_create_queues_mail_and_hides_the_token(api, db_engine, mail_env):
    world = make_world(db_engine)
    before = utcnow()
    response = _create(api, world, {"email": "  JiSu@Example.COM "})
    assert response.status_code == 201, response.text
    body = response.json()
    invitation = body["invitation"]
    assert body["deliveryStatus"] == "QUEUED"
    assert invitation["email"] == WORKER_EMAIL and invitation["status"] == "PENDING"
    assert invitation["storeId"] == world.store_id and invitation["accessExpiresAt"] is None
    created = datetime.fromisoformat(invitation["createdAt"])
    assert created >= before
    assert datetime.fromisoformat(invitation["expiresAt"]) - created == timedelta(days=7)
    assert invitation["lastSentAt"] == invitation["createdAt"]
    # The mail went out after the response, through the memory sender, with the link in a fragment.
    [message] = mail_env.messages
    token = token_of(message)
    assert message.to == WORKER_EMAIL and len(token) >= 43
    assert token not in response.text and FRONTEND not in response.text
    [row] = _rows(db_engine, StoreInvitation)
    assert row.token_hash == hash_invitation_token(token)
    [outbox] = _rows(db_engine, InvitationMailOutbox)
    assert outbox.status == "SENT" and outbox.payload is None and outbox.processed_at is not None


def test_outbox_payload_is_encrypted(api, db_engine, monkeypatch, mail_env):
    monkeypatch.setenv(invitation_mail.BACKEND_ENV, "disabled")  # nothing sends: the row stays queued
    world = make_world(db_engine)
    assert _create(api, world).status_code == 201
    [outbox] = _rows(db_engine, InvitationMailOutbox)
    assert outbox.status == "QUEUED" and mail_env.messages == []
    assert WORKER_EMAIL not in outbox.payload and "token" not in outbox.payload
    assert FRONTEND not in outbox.payload
    # A later delivery run sends it.
    assert invitation_mail.deliver_queued_mail(sender=mail_env) == 1
    assert mail_env.messages[0].to == WORKER_EMAIL


def test_access_expiry_is_normalized_to_utc(api, db_engine):
    world = make_world(db_engine)
    local = (utcnow() + timedelta(days=30)).astimezone(UTC).replace(microsecond=0)
    kst = local.astimezone(__import__("zoneinfo").ZoneInfo("Asia/Seoul")).isoformat()
    response = _create(api, world, {"email": WORKER_EMAIL, "accessExpiresAt": kst})
    assert response.status_code == 201
    stored = datetime.fromisoformat(response.json()["invitation"]["accessExpiresAt"])
    assert stored == local and stored.utcoffset() == timedelta(0)


@pytest.mark.parametrize("value", [
    "2020-01-01T00:00:00Z", "2026-11-01", "2026-11-01T00:00:00", 1767225600, "not-a-date", "",
])
def test_access_expiry_must_be_a_future_instant(api, db_engine, value):
    world = make_world(db_engine)
    response = _create(api, world, {"email": WORKER_EMAIL, "accessExpiresAt": value})
    assert response.status_code == 422
    assert response.json()["fieldErrors"][0]["field"] == "accessExpiresAt"
    assert _rows(db_engine, StoreInvitation) == []


def test_access_expiry_equal_to_now_is_rejected(api, db_engine, monkeypatch):
    world = make_world(db_engine)
    now = utcnow().replace(microsecond=0) + timedelta(days=1)
    monkeypatch.setattr(invitations, "utcnow", lambda: now)
    response = _create(api, world, {"email": WORKER_EMAIL, "accessExpiresAt": now.isoformat()})
    assert response.status_code == 422
    later = _create(api, world, {"email": WORKER_EMAIL, "accessExpiresAt": (now + timedelta(microseconds=1)).isoformat()})
    assert later.status_code == 201


@pytest.mark.parametrize("email", [
    "no-at-sign", "a@b", "a b@example.com", "@example.com", "a@@example.com", "",
    "a" * 245 + "@example.com", None, 5,
])
def test_invalid_email(api, db_engine, email):
    world = make_world(db_engine)
    response = _create(api, world, {"email": email})
    assert response.status_code == 422 and response.json()["code"] == "VALIDATION_ERROR"


@pytest.mark.parametrize("email", [
    "a\u200b@x.com", "a\u2060@x.com", "\ufeffa@x.com",  # zero-width characters
    "a@x.com\u00a0", "\u00a0a@x.com", "\u3000a@x.com",  # non-ASCII spaces are not trimmed
    "\uff41\uff42\uff43@x.com", "a\uff20x.com",  # full-width letters and at sign
    "jos\u00e9@x.com", "a@b\u00fccher.de", "\u0430dmin@x.com",  # accent, IDN, Cyrillic a
    "a\x7f@x.com",  # DEL is not printable
])
def test_non_ascii_email_is_refused(api, db_engine, email):
    world = make_world(db_engine)
    response = _create(api, world, {"email": email})
    assert response.status_code == 422, response.text
    body = response.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert [(error["field"], error["code"]) for error in body["fieldErrors"]] == [("email", "INVALID_FORMAT")]
    with Session(db_engine) as session:
        assert session.scalar(select(func.count()).select_from(StoreInvitation)) == 0


@pytest.mark.parametrize("email, stored", [
    (" Jisu@Example.com ", "jisu@example.com"),
    ("\tJisu@Example.com\r\n", "jisu@example.com"),  # ASCII whitespace is trimmed
    ("a+tag@x.co.kr", "a+tag@x.co.kr"),
    ("a_b%c@x.com", "a_b%c@x.com"),
])
def test_ascii_email_is_accepted_and_normalized(api, db_engine, mail_env, email, stored):
    world = make_world(db_engine)
    response = _create(api, world, {"email": email})
    assert response.status_code == 201, response.text
    assert response.json()["invitation"]["email"] == stored


def test_aliases_are_not_merged(api, db_engine, mail_env):
    world = make_world(db_engine)
    for email in ("jisu+cafe@example.com", "ji.su@example.com", WORKER_EMAIL):
        assert _create(api, world, {"email": email}).status_code == 201
    assert sorted(message.to for message in mail_env.messages) == sorted(
        ["jisu+cafe@example.com", "ji.su@example.com", WORKER_EMAIL],
    )


def test_cannot_invite_own_email(api, db_engine):
    world = make_world(db_engine)
    response = _create(api, world, {"email": " owner@EXAMPLE.com"})
    assert response.status_code == 422
    assert response.json()["fieldErrors"][0]["field"] == "email"


def test_duplicate_pending_invitation(api, db_engine):
    world = make_world(db_engine)
    assert _create(api, world).status_code == 201
    again = _create(api, world, {"email": WORKER_EMAIL.upper()})
    assert again.status_code == 409 and again.json()["code"] == "INVITATION_ALREADY_PENDING"
    # An expired or cancelled invitation does not block a new one.
    with Session(db_engine) as db:
        db.scalars(select(StoreInvitation)).one().canceled_at = utcnow()
        db.commit()
    assert _create(api, world).status_code == 201


def test_expired_pending_does_not_block(api, db_engine):
    world = make_world(db_engine)
    seed_invitation(db_engine, world.store_id, created_at=utcnow() - timedelta(days=8))
    seed_invitation(db_engine, world.store_id, access_expires_at=utcnow() - timedelta(minutes=1))
    assert _create(api, world).status_code == 201


def test_existing_valid_access_blocks_but_ended_access_does_not(api, db_engine):
    world = make_world(db_engine)
    with Session(db_engine) as db:
        from app.db.models import Store, User
        grant = make_regular_grant(db, db.get(Store, world.store_id), db.get(User, world.worker_id),
                                   granted_at=utcnow() - timedelta(days=1))
        db.commit()
        grant_id = grant.id
    response = _create(api, world, {"email": WORKER_EMAIL.upper()})
    assert response.status_code == 409 and response.json()["code"] == "WORKER_ALREADY_HAS_ACCESS"
    with Session(db_engine) as db:
        db.get(StoreAccessGrant, grant_id).revoked_at = utcnow()
        db.commit()
    assert _create(api, world).status_code == 201



def test_temporary_shift_access_does_not_block_a_regular_invitation(api, db_engine):
    # A confirmed substitute shift's TEMPORARY access coexists with regular access.
    world = make_world(db_engine)
    with Session(db_engine) as db:
        from app.db.models import Store, User
        make_temporary_grant(db, db.get(Store, world.store_id), db.get(User, world.worker_id),
                             granted_at=utcnow() - timedelta(hours=1), valid_until=utcnow() + timedelta(hours=3))
        db.commit()
    assert _create(api, world).status_code == 201

def test_access_in_another_store_does_not_block(api, db_engine):
    world = make_world(db_engine)
    with Session(db_engine) as db:
        from app.db.models import User
        other, _ = make_store_with_request(db, approved_at=utcnow() - timedelta(days=2))
        make_regular_grant(db, other, db.get(User, world.worker_id), granted_at=utcnow() - timedelta(days=1))
        db.commit()
    assert _create(api, world).status_code == 201


def test_create_replay_does_not_send_twice(api, db_engine, mail_env):
    world = make_world(db_engine)
    key = new_key()
    first = _create(api, world, key=key)
    replay = _create(api, world, key=key)
    assert replay.status_code == 201 and replay.json() == first.json()
    assert replay.headers["Idempotent-Replayed"] == "true"
    assert len(mail_env.messages) == 1 and len(_rows(db_engine, InvitationMailOutbox)) == 1
    other = _create(api, world, {"email": "other@example.com"}, key=key)
    assert other.status_code == 409 and other.json()["code"] == "IDEMPOTENCY_KEY_REUSED"


def test_replay_rechecks_store_access(api, db_engine):
    world = make_world(db_engine)
    key = new_key()
    assert _create(api, world, key=key).status_code == 201
    with Session(db_engine) as db:
        from app.db.models import Store
        store = db.get(Store, world.store_id)
        store.approval_status, store.approved_at = "PENDING", None
        db.commit()
    replay = _create(api, world, key=key)
    assert replay.status_code == 403 and replay.json()["code"] == "STORE_APPROVAL_REQUIRED"


@pytest.mark.parametrize("missing", [invitation_mail.KEY_ENV, invitation_mail.FRONTEND_ORIGIN_ENV])
def test_queue_unavailable_rolls_back(api, db_engine, monkeypatch, missing):
    world = make_world(db_engine)
    monkeypatch.delenv(missing)
    key = new_key()
    response = _create(api, world, key=key)
    assert response.status_code == 503 and response.json()["code"] == "DELIVERY_UNAVAILABLE"
    assert response.json()["message"] == "초대 메일 발송을 요청하지 못했습니다. 잠시 후 다시 시도해 주세요."
    assert _rows(db_engine, StoreInvitation) == [] and _rows(db_engine, InvitationMailOutbox) == []
    monkeypatch.setenv(invitation_mail.KEY_ENV, __import__("cryptography.fernet").fernet.Fernet.generate_key().decode())
    monkeypatch.setenv(invitation_mail.FRONTEND_ORIGIN_ENV, FRONTEND)
    assert _create(api, world, key=key).status_code == 201  # the key was released


def test_create_requires_approved_owned_store(api, db_engine):
    pending = make_world(db_engine, approved=False)
    response = _create(api, pending)
    assert response.status_code == 403 and response.json()["code"] == "STORE_APPROVAL_REQUIRED"
    world = make_world(db_engine, worker_email="other@example.com")
    session = login(api, world.owner_id)
    for store_id in (pending.store_id, MISSING):
        response = api.post(f"/api/stores/{store_id}/invitations", json={"email": WORKER_EMAIL},
                            headers=session.headers(new_key()))
        assert response.status_code == 404 and response.json()["code"] == "STORE_NOT_FOUND"
    response = api.post("/api/stores/bad/invitations", json={"email": WORKER_EMAIL}, headers=session.headers(new_key()))
    assert response.status_code == 422


def test_create_auth_and_csrf(api, db_engine):
    world = make_world(db_engine)
    assert api.post(_path(world), json={"email": WORKER_EMAIL}, headers={"Origin": "http://frontend.test"}).status_code == 401
    worker = login(api, world.worker_id)
    response = api.post(_path(world), json={"email": WORKER_EMAIL}, headers=worker.headers(new_key()))
    assert response.status_code == 403 and response.json()["code"] == "FORBIDDEN"
    owner = login(api, world.owner_id)
    headers = owner.headers(new_key())
    headers["X-CSRF-Token"] = "nope"
    response = api.post(_path(world), json={"email": WORKER_EMAIL}, headers=headers)
    assert response.status_code == 403 and response.json()["code"] == "CSRF_INVALID"
    response = api.post(_path(world), json={"email": WORKER_EMAIL}, headers=owner.headers())
    assert response.status_code == 422
    response = api.post(_path(world), json={"email": WORKER_EMAIL, "storeId": MISSING}, headers=owner.headers(new_key()))
    assert response.status_code == 422
    response = api.post(_path(world), content=b"{", headers={**owner.headers(new_key()), "Content-Type": "application/json"})
    assert response.status_code == 400
    assert _rows(db_engine, StoreInvitation) == []


@pytest.mark.mysql
@pytest.mark.parametrize("db_engine", ["mysql"], indirect=True)  # row locks: MySQL only
def test_concurrent_creates_for_one_email_make_one_invitation(db_engine, monkeypatch):
    from fastapi.testclient import TestClient

    from app.auth import SESSION_COOKIE_NAME
    from app.main import app
    from tests.api_contract import ORIGIN, validate_response

    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("ALLOWED_ORIGINS", ORIGIN)
    world = make_world(db_engine)
    with TestClient(app) as probe:
        session = login(probe, world.owner_id)
    barrier = threading.Barrier(4)
    results = []

    def submit():
        with TestClient(app, raise_server_exceptions=False) as client:
            client.cookies.set(SESSION_COOKIE_NAME, session.token)
            barrier.wait(5)
            response = client.post(_path(world), json={"email": WORKER_EMAIL}, headers=session.headers(new_key()))
            validate_response(response)
            results.append(response.json().get("code", response.status_code))

    threads = [threading.Thread(target=submit) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    assert sorted(map(str, results)) == ["201", "INVITATION_ALREADY_PENDING", "INVITATION_ALREADY_PENDING", "INVITATION_ALREADY_PENDING"]
    assert len(_rows(db_engine, StoreInvitation)) == 1


# --- GET /api/stores/{storeId}/invitations ---------------------------------------------------------

def test_list_tabs_counts_and_derived_status(api, db_engine, monkeypatch):
    world = make_world(db_engine)
    now = utcnow()
    monkeypatch.setattr(invitations, "utcnow", lambda: now)
    hour = timedelta(hours=1)
    ids = {}
    ids["pending"], _ = seed_invitation(db_engine, world.store_id, email="a@example.com", created_at=now - hour)
    ids["pending_limited"], _ = seed_invitation(db_engine, world.store_id, email="b@example.com",
                                                created_at=now - 2 * hour, access_expires_at=now + hour)
    ids["link_expired"], _ = seed_invitation(db_engine, world.store_id, email="c@example.com",
                                             created_at=now - timedelta(days=7))  # expires exactly now
    ids["access_ended"], _ = seed_invitation(db_engine, world.store_id, email="d@example.com",
                                             created_at=now - 4 * hour, access_expires_at=now)
    ids["accepted"], _ = seed_invitation(db_engine, world.store_id, email=WORKER_EMAIL, created_at=now - 5 * hour,
                                         accepted_by_worker_id=world.worker_id, accepted_at=now - 4 * hour)
    ids["declined"], _ = seed_invitation(db_engine, world.store_id, email="e@example.com", created_at=now - 6 * hour,
                                         declined_by_worker_id=world.worker_id, declined_at=now - 5 * hour)
    ids["cancelled"], _ = seed_invitation(db_engine, world.store_id, email="f@example.com",
                                          created_at=now - timedelta(days=9), canceled_at=now - timedelta(days=8))
    other = make_world(db_engine, worker_email="z@example.com")
    seed_invitation(db_engine, other.store_id, email="g@example.com")
    login(api, world.owner_id)
    active = api.get(_path(world)).json()
    assert [item["id"] for item in active["items"]] == [ids["pending"], ids["pending_limited"]]
    assert (active["activeCount"], active["pastCount"], active["totalItems"]) == (2, 5, 2)
    assert datetime.fromisoformat(active["asOf"]) == now
    past = api.get(_path(world, "?view=PAST")).json()
    statuses = {item["id"]: item["status"] for item in past["items"]}
    assert [item["id"] for item in past["items"]] == [
        ids["access_ended"], ids["accepted"], ids["declined"], ids["link_expired"], ids["cancelled"],
    ]
    assert statuses == {
        ids["link_expired"]: "EXPIRED", ids["access_ended"]: "EXPIRED", ids["accepted"]: "ACCEPTED",
        ids["declined"]: "DECLINED", ids["cancelled"]: "CANCELLED",
    }
    accepted = next(item for item in past["items"] if item["id"] == ids["accepted"])
    assert accepted["acceptedBy"] == {"workerId": world.worker_id, "name": "테스터"}
    assert (past["activeCount"], past["pastCount"], past["totalItems"]) == (2, 5, 5)
    page = api.get(_path(world, "?view=PAST&page=1&size=2")).json()
    assert [item["id"] for item in page["items"]] == [ids["declined"], ids["link_expired"]]
    assert page["totalPages"] == 3
    assert api.get(_path(world, "?view=PAST&page=9")).json()["items"] == []


def test_list_empty_and_validation(api, db_engine):
    world = make_world(db_engine)
    login(api, world.owner_id)
    body = api.get(_path(world)).json()
    assert (body["items"], body["activeCount"], body["pastCount"]) == ([], 0, 0)
    for query in ("?view=ALL", "?view=active", "?size=101", "?page=-1"):
        response = api.get(_path(world, query))
        assert response.status_code == 422, query


def test_list_access_rules(api, db_engine):
    world = make_world(db_engine)
    pending = make_world(db_engine, approved=False, worker_email="x@example.com")
    login(api, pending.owner_id)
    assert api.get(_path(pending)).json()["code"] == "STORE_APPROVAL_REQUIRED"
    assert api.get(_path(world)).json()["code"] == "STORE_NOT_FOUND"
    login(api, world.worker_id)
    assert api.get(_path(world)).json()["code"] == "FORBIDDEN"
    api.cookies.clear()
    assert api.get(_path(world)).status_code == 401


# --- POST .../{invitationId}/resend ---------------------------------------------------------------

def _resend(api, world, invitation_id, key=None):
    session = login(api, world.owner_id)
    return api.post(_path(world, f"/{invitation_id}/resend"), headers=session.headers(key or new_key()))


def test_resend_replaces_the_token_and_keeps_deadlines(api, db_engine, mail_env):
    world = make_world(db_engine)
    first = _create(api, world, {"email": WORKER_EMAIL, "accessExpiresAt": (utcnow() + timedelta(days=30)).isoformat()})
    invitation = first.json()["invitation"]
    old_token = token_of(mail_env.messages[0])
    response = _resend(api, world, invitation["id"])
    assert response.status_code == 200, response.text
    resent = response.json()["invitation"]
    assert response.json()["deliveryStatus"] == "QUEUED"
    for field in ("id", "email", "createdAt", "expiresAt", "accessExpiresAt", "status"):
        assert resent[field] == invitation[field]
    assert resent["lastSentAt"] > invitation["lastSentAt"]
    new_token = token_of(mail_env.messages[1])
    assert new_token != old_token
    with Session(db_engine) as db:
        assert db.get(StoreInvitation, invitation["id"]).token_hash == hash_invitation_token(new_token)


def test_resend_discards_unsent_mail(api, db_engine, monkeypatch):
    monkeypatch.setenv(invitation_mail.BACKEND_ENV, "disabled")
    world = make_world(db_engine)
    invitation_id = _create(api, world).json()["invitation"]["id"]
    assert _resend(api, world, invitation_id).status_code == 200
    statuses = sorted(row.status for row in _rows(db_engine, InvitationMailOutbox))
    assert statuses == ["DISCARDED", "QUEUED"]
    discarded = next(row for row in _rows(db_engine, InvitationMailOutbox) if row.status == "DISCARDED")
    assert discarded.payload is None


def test_resend_replay_sends_no_extra_mail(api, db_engine, mail_env):
    world = make_world(db_engine)
    invitation_id = _create(api, world).json()["invitation"]["id"]
    key = new_key()
    first = _resend(api, world, invitation_id, key)
    replay = _resend(api, world, invitation_id, key)
    assert replay.json() == first.json() and replay.headers["Idempotent-Replayed"] == "true"
    assert len(mail_env.messages) == 2


@pytest.mark.parametrize("state,status,code", [
    ({"accepted": True}, 409, "INVITATION_STATE_CONFLICT"),
    ({"declined": True}, 409, "INVITATION_STATE_CONFLICT"),
    ({"canceled": True}, 409, "INVITATION_STATE_CONFLICT"),
    ({"link_expired": True}, 410, "INVITATION_EXPIRED"),
    ({"access_ended": True}, 410, "ACCESS_PERIOD_ENDED"),
])
def test_resend_and_cancel_refuse_non_pending(api, db_engine, state, status, code):
    world = make_world(db_engine)
    now = utcnow()
    kwargs = {}
    if state.get("accepted"):
        kwargs.update(accepted_by_worker_id=world.worker_id, accepted_at=now - timedelta(minutes=1))
    if state.get("declined"):
        kwargs.update(declined_by_worker_id=world.worker_id, declined_at=now - timedelta(minutes=1))
    if state.get("canceled"):
        kwargs.update(canceled_at=now - timedelta(minutes=1))
    if state.get("link_expired"):
        kwargs.update(created_at=now - timedelta(days=7, seconds=1))
    if state.get("access_ended"):
        kwargs.update(access_expires_at=now - timedelta(seconds=1))
    invitation_id, token = seed_invitation(db_engine, world.store_id, **kwargs)
    response = _resend(api, world, invitation_id)
    assert (response.status_code, response.json()["code"]) == (status, code)
    with Session(db_engine) as db:
        assert db.get(StoreInvitation, invitation_id).token_hash == hash_invitation_token(token)
    session = login(api, world.owner_id)
    cancel = api.post(_path(world, f"/{invitation_id}/cancel"), headers=session.headers())
    if state.get("canceled"):
        assert cancel.status_code == 200 and cancel.json()["status"] == "CANCELLED"
    else:
        assert (cancel.status_code, cancel.json()["code"]) == (status, code)


def test_resend_queue_failure_keeps_the_old_link(api, db_engine, monkeypatch):
    world = make_world(db_engine)
    invitation_id, token = seed_invitation(db_engine, world.store_id)
    monkeypatch.delenv(invitation_mail.KEY_ENV)
    response = _resend(api, world, invitation_id)
    assert response.status_code == 503
    assert response.json()["message"] == "재전송을 요청하지 못했습니다. 잠시 후 다시 시도해 주세요."
    with Session(db_engine) as db:
        assert db.get(StoreInvitation, invitation_id).token_hash == hash_invitation_token(token)


def test_resend_and_cancel_hide_other_store_invitations(api, db_engine):
    world = make_world(db_engine)
    other = make_world(db_engine, worker_email="o@example.com")
    foreign_id, _ = seed_invitation(db_engine, other.store_id)
    for target in (foreign_id, MISSING):
        response = _resend(api, world, target)
        assert response.status_code == 404 and response.json()["code"] == "INVITATION_NOT_FOUND"
        session = login(api, world.owner_id)
        response = api.post(_path(world, f"/{target}/cancel"), headers=session.headers())
        assert response.status_code == 404 and response.json()["code"] == "INVITATION_NOT_FOUND"
    session = login(api, world.owner_id)
    assert api.post(_path(world, "/bad/cancel"), headers=session.headers()).status_code == 422
    # Another owner's store stays STORE_NOT_FOUND even with a real invitation id.
    response = api.post(f"/api/stores/{other.store_id}/invitations/{foreign_id}/cancel", headers=session.headers())
    assert response.json()["code"] == "STORE_NOT_FOUND"


# --- POST .../{invitationId}/cancel ---------------------------------------------------------------

def test_cancel_keeps_first_time_and_blocks_the_link(api, db_engine, monkeypatch):
    monkeypatch.setenv(invitation_mail.BACKEND_ENV, "disabled")
    world = make_world(db_engine)
    created = _create(api, world).json()["invitation"]
    session = login(api, world.owner_id)
    first = api.post(_path(world, f"/{created['id']}/cancel"), headers=session.headers())
    assert first.status_code == 200 and first.json()["status"] == "CANCELLED"
    assert first.json()["canceledAt"] is not None
    [outbox] = _rows(db_engine, InvitationMailOutbox)
    assert outbox.status == "DISCARDED" and outbox.payload is None
    later = utcnow() + timedelta(days=10)
    monkeypatch.setattr(invitations, "utcnow", lambda: later)
    again = api.post(_path(world, f"/{created['id']}/cancel"), headers=session.headers())
    assert again.status_code == 200 and again.json() == first.json()


def test_cancel_requires_csrf(api, db_engine):
    world = make_world(db_engine)
    invitation_id, _ = seed_invitation(db_engine, world.store_id)
    login(api, world.owner_id)
    response = api.post(_path(world, f"/{invitation_id}/cancel"), headers={"Origin": "http://frontend.test"})
    assert response.status_code == 403 and response.json()["code"] == "CSRF_INVALID"


def test_counts_after_lifecycle(api, db_engine):
    world = make_world(db_engine)
    _create(api, world)
    login(api, world.owner_id)
    summary = api.get(f"/api/stores/{world.store_id}/management-summary").json()
    listing = api.get(_path(world)).json()
    assert summary["pendingInvitationCount"] == listing["activeCount"] == 1
    with Session(db_engine) as db:
        assert db.scalar(select(func.count()).select_from(StoreInvitation)) == 1
