"""Store approval and invitation transitions record their notifications (#116 wiring).
"""

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import admin_password
from app.db.models import Notification, StoreInvitation, User
from app.ratelimit import reset_all_limits
from tests.api_contract import ORIGIN, login
from tests.factories import make_store_with_request, make_user, make_worker
from tests.invitation_helpers import (
    WORKER_EMAIL,
    configure_mail,
    make_world,
    new_key,
    seed_invitation,
)
from tests.jobs_support import pin_clock, seed_shop
from tests.test_work_requests import Scene


@pytest.fixture
def clock(monkeypatch):
    return pin_clock(monkeypatch)


@pytest.fixture
def scene(api, db_engine, clock) -> Scene:
    shop = seed_shop(db_engine, name="월계 카페")
    return Scene(api, db_engine, clock, shop.store_id, shop.owner_id)


@pytest.fixture(autouse=True)
def mail_env(monkeypatch):
    return configure_mail(monkeypatch)


def notes(db_engine, kind=None) -> list[Notification]:
    with Session(db_engine) as db:
        statement = select(Notification).order_by(Notification.created_at, Notification.id)
        if kind is not None:
            statement = statement.where(Notification.event_type == kind)
        return list(db.scalars(statement))


def one(db_engine, kind) -> Notification:
    rows = notes(db_engine, kind)
    assert len(rows) == 1, [(r.event_type, r.recipient_user_id) for r in rows]
    return rows[0]


# --- store approval -------------------------------------------------------------------------------

PASSWORD = "correct horse battery staple"


@pytest.fixture
def admin(monkeypatch):
    monkeypatch.setenv(admin_password.HASH_ENV, admin_password.hash_password(PASSWORD, log2_n=admin_password.MIN_LOG2_N))
    reset_all_limits()
    yield
    reset_all_limits()


def approve(api, request_id):
    return api.post(f"/api/admin/store-approval-requests/{request_id}/approve", json={"password": PASSWORD},
                    headers={"Origin": ORIGIN})


def test_store_approval_notifies_the_owner_once(api, db_engine, admin):
    with Session(db_engine) as db:
        owner = make_user(db, "OWNER")
        store, request = make_store_with_request(db, owner, name="월계 분식")
        db.commit()
        owner_id, store_id, request_id = owner.id, store.id, request.id
    assert approve(api, request_id).status_code == 200
    assert approve(api, request_id).status_code == 200  # already approved: same result, no new row
    row = one(db_engine, "STORE_APPROVED")
    assert row.recipient_user_id == owner_id and row.target_context == {"type": "STORE", "storeId": store_id}
    assert row.body == "월계 분식 매장 승인이 완료됐어요"


def test_refused_approval_records_nothing(api, db_engine, admin):
    with Session(db_engine) as db:
        _store, request = make_store_with_request(db, make_user(db, "WORKER"))
        db.commit()
        request_id = request.id
    assert approve(api, request_id).status_code == 409
    assert notes(db_engine) == []


# --- invitations ---------------------------------------------------------------------------------


def create_invitation(api, world, email=WORKER_EMAIL, idem=None):
    owner = login(api, world.owner_id)
    return api.post(f"/api/stores/{world.store_id}/invitations", json={"email": email},
                    headers=owner.headers(idem or new_key()))


def test_invited_member_is_notified_without_the_token(api, db_engine):
    world = make_world(db_engine, worker_email="Jisu@Example.com ")
    idem = new_key()
    created = create_invitation(api, world, idem=idem)
    assert created.status_code == 201
    assert create_invitation(api, world, idem=idem).headers["Idempotent-Replayed"] == "true"
    row = one(db_engine, "STORE_INVITED")
    invitation_id = created.json()["invitation"]["id"]
    assert row.recipient_user_id == world.worker_id
    assert row.target_context == {"type": "STORE_INVITATION", "invitationId": invitation_id}
    with Session(db_engine) as db:
        token_hash = db.get(StoreInvitation, invitation_id).token_hash
    assert token_hash not in str(row.target_context) + row.body and "@" not in row.body


def test_resend_notifies_again_and_its_replay_does_not(api, db_engine):
    world = make_world(db_engine)
    invitation_id = create_invitation(api, world).json()["invitation"]["id"]
    owner = login(api, world.owner_id)
    url = f"/api/stores/{world.store_id}/invitations/{invitation_id}/resend"
    idem = new_key()
    assert api.post(url, headers=owner.headers(idem)).status_code == 200
    assert api.post(url, headers=owner.headers(idem)).headers["Idempotent-Replayed"] == "true"
    rows = notes(db_engine, "STORE_INVITED")
    assert len(rows) == 2 and len({r.dedupe_key for r in rows}) == 2
    assert {r.target_id for r in rows} == {invitation_id}


@pytest.mark.parametrize("who", ["nobody", "owner", "unverified", "suspended"])
def test_only_active_verified_workers_with_the_email_are_notified(api, db_engine, who):
    world = make_world(db_engine, worker_email="someone-else@example.com")
    with Session(db_engine) as db:
        if who == "owner":
            make_user(db, "OWNER", google_email=WORKER_EMAIL)
        elif who == "unverified":
            worker = make_worker(db)
            worker.google_email, worker.email_verified = WORKER_EMAIL, False
        elif who == "suspended":
            worker = make_worker(db)
            worker.google_email, worker.status = WORKER_EMAIL, "SUSPENDED"
        db.commit()
    assert create_invitation(api, world).status_code == 201
    assert notes(db_engine, "STORE_INVITED") == []


def test_email_wildcards_are_literal(api, db_engine):
    world = make_world(db_engine, worker_email="a_b@example.com")
    with Session(db_engine) as db:
        lookalike = make_worker(db)
        lookalike.google_email = "axb@example.com"
        db.commit()
    assert create_invitation(api, world, email="a_b@example.com").status_code == 201
    assert [r.recipient_user_id for r in notes(db_engine, "STORE_INVITED")] == [world.worker_id]


def test_failed_invitation_delivery_rolls_back_the_notification(api, db_engine, monkeypatch):
    from app import invitation_mail

    world = make_world(db_engine)
    monkeypatch.delenv(invitation_mail.KEY_ENV)
    assert create_invitation(api, world).status_code == 503
    assert notes(db_engine) == []


def test_acceptance_notifies_the_owner_once(api, db_engine):
    world = make_world(db_engine)
    _invitation_id, token = seed_invitation(db_engine, world.store_id)
    worker = login(api, world.worker_id)
    for _ in range(2):  # the second accept is the same worker's replay
        response = api.post("/api/store-invitations/accept", json={"token": token}, headers=worker.headers())
        assert response.status_code == 200, response.text
    row = one(db_engine, "INVITATION_ACCEPTED")
    assert row.recipient_user_id == world.owner_id
    assert row.target_context == {"type": "STORE", "storeId": world.store_id}
    with Session(db_engine) as db:
        assert db.get(User, world.worker_id).name not in row.body




def test_failure_after_recording_rolls_the_invitation_notification_back(api, db_engine, monkeypatch):
    from app import invitations

    world = make_world(db_engine)

    def fail(*_args, **_kwargs):
        raise RuntimeError("fails after STORE_INVITED was recorded")

    monkeypatch.setattr(invitations, "invitation_body", fail)
    assert create_invitation(api, world).status_code == 500
    assert notes(db_engine) == []
    with Session(db_engine) as db:
        assert db.scalars(select(StoreInvitation)).all() == []


# --- INVITATION_ACCEPTED on both response paths (#108 token, #115 inbox) ---------------------------

INBOX = "/api/users/me/store-invitations"


def inbox_respond(api, worker, invitation_id, decision="ACCEPT", idem=None):
    return api.post(f"{INBOX}/{invitation_id}/response", json={"decision": decision},
                    headers=worker.headers(idem or new_key()))


def grant_count(db_engine) -> int:
    from app.db.models import StoreAccessGrant

    with Session(db_engine) as db:
        return len(db.scalars(select(StoreAccessGrant)).all())


def test_inbox_acceptance_records_one_notification_committed_by_run_idempotent(api, db_engine):
    world = make_world(db_engine)
    invitation_id, token = seed_invitation(db_engine, world.store_id)
    worker = login(api, world.worker_id)
    idem = new_key()
    first = inbox_respond(api, worker, invitation_id, idem=idem)
    assert first.status_code == 200, first.text
    replay = inbox_respond(api, worker, invitation_id, idem=idem)
    assert replay.headers["Idempotent-Replayed"] == "true"
    assert inbox_respond(api, worker, invitation_id).status_code in (200, 409)  # a new key
    via_token = api.post("/api/store-invitations/accept", json={"token": token}, headers=worker.headers())
    assert via_token.status_code == 200
    row = one(db_engine, "INVITATION_ACCEPTED")
    assert row.recipient_user_id == world.owner_id and row.dedupe_key == f"INVITATION_ACCEPTED:{invitation_id}"
    assert grant_count(db_engine) == 1


def test_inbox_decline_records_nothing(api, db_engine):
    world = make_world(db_engine)
    invitation_id, _token = seed_invitation(db_engine, world.store_id)
    assert inbox_respond(api, login(api, world.worker_id), invitation_id, "DECLINE").status_code == 200
    assert notes(db_engine, "INVITATION_ACCEPTED") == []


@pytest.mark.parametrize("path", ["token", "inbox"])
def test_failure_after_recording_rolls_the_acceptance_back(api, db_engine, monkeypatch, path):
    """The notification is written before either commit point (respond_to_invitation's own
    commit for the token path, run_idempotent's for the inbox), so a later failure undoes both."""
    from app import invitation_responses

    world = make_world(db_engine)
    invitation_id, token = seed_invitation(db_engine, world.store_id)
    worker = login(api, world.worker_id)

    def fail(*_args, **_kwargs):
        raise RuntimeError("fails after INVITATION_ACCEPTED was recorded")

    original = invitation_responses._acceptance_body
    monkeypatch.setattr(invitation_responses, "_acceptance_body", fail)
    if path == "token":
        response = api.post("/api/store-invitations/accept", json={"token": token}, headers=worker.headers())
    else:
        response = inbox_respond(api, worker, invitation_id)
    assert response.status_code == 500
    assert notes(db_engine) == [] and grant_count(db_engine) == 0
    with Session(db_engine) as db:
        assert db.get(StoreInvitation, invitation_id).accepted_at is None
    monkeypatch.setattr(invitation_responses, "_acceptance_body", original)
    retry = inbox_respond(api, worker, invitation_id)  # the key was released; the state is intact
    assert retry.status_code == 200 and len(notes(db_engine, "INVITATION_ACCEPTED")) == 1


@pytest.mark.mysql
@pytest.mark.parametrize("db_engine", ["mysql"], indirect=True)
def test_race_token_and_inbox_acceptance_notify_once(api, db_engine):
    from tests.jobs_support import client_factory, race

    for _ in range(3):
        world = make_world(db_engine, worker_email=f"{new_key()[:8]}@example.com")
        invitation_id, token = seed_invitation(db_engine, world.store_id, email=world.worker_email)
        worker = login(api, world.worker_id)
        responses = race([
            (client_factory(worker.token), lambda c, t=token, w=worker: c.post(
                "/api/store-invitations/accept", json={"token": t}, headers=w.headers())),
            (client_factory(worker.token), lambda c, i=invitation_id, w=worker: c.post(
                f"{INBOX}/{i}/response", json={"decision": "ACCEPT"}, headers=w.headers(new_key()))),
        ])
        assert 200 in [r.status_code for r in responses], [r.text for r in responses]
        with Session(db_engine) as db:
            rows = db.scalars(select(Notification).where(
                Notification.event_type == "INVITATION_ACCEPTED",
                Notification.dedupe_key == f"INVITATION_ACCEPTED:{invitation_id}",
            )).all()
        assert len(rows) == 1 and rows[0].recipient_user_id == world.owner_id


@pytest.mark.parametrize(("member", "invited"), [("josé@example.com", "jose@example.com"),
                                                 ("strasse@example.de", "straße@example.de")])
def test_inbox_matches_the_same_exact_email_as_the_notification(api, db_engine, member, invited):
    """MySQL's old collation folded accents and ß; the inbox must not show someone else's
    invitation that the notification (and acceptance) correctly refuse. A non-ASCII address can
    no longer be invited (422), so such a row is seeded as one stored before that rule."""
    world = make_world(db_engine, worker_email=member)
    if invited.isascii():
        created = create_invitation(api, world, email=invited)
        assert created.status_code == 201, created.text
        invitation_id = created.json()["invitation"]["id"]
    else:
        assert create_invitation(api, world, email=invited).status_code == 422
        invitation_id, _token = seed_invitation(db_engine, world.store_id, email=invited)
    assert notes(db_engine, "STORE_INVITED") == []
    login(api, world.worker_id)
    listed = api.get("/api/users/me/store-invitations", params={"filter": "ALL"})
    assert listed.status_code == 200 and listed.json()["items"] == []
    assert api.get(f"/api/users/me/store-invitations/{invitation_id}").status_code == 404


def test_owner_checks_do_not_fold_accents(api, db_engine):
    """A pending invitation or a live access of 'josé@' is not one of 'jose@' (MySQL folds them)."""
    from datetime import timedelta

    from app.db import utcnow
    from app.db.models import StoreAccessGrant

    world = make_world(db_engine, worker_email="josé@example.com")
    assert create_invitation(api, world, email="josé@example.com").status_code == 422  # ASCII only now
    seed_invitation(db_engine, world.store_id, email="josé@example.com")  # stored before that rule
    assert create_invitation(api, world, email="jose@example.com").status_code == 201  # not ALREADY_PENDING
    with Session(db_engine) as db:  # the accented member gets access at the store directly
        invitations = list(db.scalars(select(StoreInvitation).where(StoreInvitation.store_id == world.store_id)))
        invitation = next(i for i in invitations if i.invited_email == "josé@example.com")
        db.add(StoreAccessGrant(store_id=world.store_id, worker_id=world.worker_id, invitation_id=invitation.id,
                                granted_at=utcnow() - timedelta(minutes=1)))
        invitation.accepted_at, invitation.accepted_by_worker_id = utcnow(), world.worker_id
        db.commit()
    with Session(db_engine) as db:  # make room for a new invitation to the unaccented address
        for row in db.scalars(select(StoreInvitation).where(StoreInvitation.store_id == world.store_id)):
            if row.invited_email == "jose@example.com":  # exact, in Python (MySQL `=` folds accents)
                row.canceled_at = utcnow()
        db.commit()
    response = create_invitation(api, world, email="jose@example.com")
    assert response.status_code == 201, response.text  # not WORKER_ALREADY_HAS_ACCESS


def test_accent_variant_accounts_stay_two_people(api, db_engine):
    """#105: identity is google_sub and google_email has no UNIQUE, so 'jose@' and 'josé@' register
    as two members. Each sees only the invitation sent to their own address; the accented one is
    seeded (stored before the ASCII-only rule), so only the plain one was notified on creation."""
    world = make_world(db_engine, worker_email="jose@example.com")
    with Session(db_engine) as db:
        accented = make_worker(db)
        accented.google_email = "josé@example.com"
        db.commit()
        accented_id = accented.id
    plain = create_invitation(api, world, email="jose@example.com").json()["invitation"]["id"]
    assert create_invitation(api, world, email="josé@example.com").status_code == 422
    other, _token = seed_invitation(db_engine, world.store_id, email="josé@example.com")
    assert {(r.recipient_user_id, r.target_id) for r in notes(db_engine, "STORE_INVITED")} == {
        (world.worker_id, plain)}
    for member, own in ((world.worker_id, plain), (accented_id, other)):
        login(api, member)
        items = api.get("/api/users/me/store-invitations", params={"filter": "ALL"}).json()["items"]
        assert [i["id"] for i in items] == [own]
