"""GET /api/users/me/notifications and POST .../{notificationId}/read against openapi.yaml."""
import threading
import uuid
from datetime import timedelta

import pytest
from sqlalchemy.orm import Session

from app.auth import SESSION_COOKIE_NAME
from app.db.models import Notification, User
from app.notifications import NotificationType, StoreInvitationTarget
from tests.api_contract import ORIGIN, login
from tests.factories import (
    NOW,
    make_invitation,
    make_notification,
    make_store,
    make_user,
    make_worker,
)

LIST = "/api/users/me/notifications"


def seed(db_engine, build):
    """Run `build(session)` in a committed transaction and return its result."""
    with Session(db_engine, expire_on_commit=False) as session:
        result = build(session)
        session.commit()
        return result


def notify(session, user, *, minutes=0, read=False, **overrides):
    row = make_notification(session, user, created_at=NOW + timedelta(minutes=minutes), **overrides)
    if read:
        row.read_at = row.created_at + timedelta(seconds=1)
    return row


def worker_with_one(session, **options) -> User:
    worker = make_worker(session)
    notify(session, worker, **options)
    return worker


def test_requires_a_member_session(api):
    response = api.get(LIST)
    assert response.status_code == 401 and response.json()["code"] == "SESSION_EXPIRED"
    api.cookies.set(SESSION_COOKIE_NAME, "forged")
    assert api.get(LIST).status_code == 401


def test_suspended_member_is_forbidden(api, db_engine):
    user = seed(db_engine, make_worker)
    login(api, user.id)
    seed(db_engine, lambda s: setattr(s.get(User, user.id), "status", "SUSPENDED"))
    response = api.get(LIST)
    assert response.status_code == 403 and response.json()["code"] == "ACCOUNT_SUSPENDED"


@pytest.mark.parametrize("role", ["WORKER", "OWNER"])
def test_empty_list_for_either_role(api, db_engine, role):
    user = seed(db_engine, lambda s: make_user(s, role))
    login(api, user.id)
    body = api.get(LIST).json()
    assert body["items"] == [] and body["totalItems"] == 0 and body["unreadCount"] == 0
    assert (body["page"], body["size"]) == (0, 20)


def test_lists_only_own_notifications_newest_first_with_id_tie_break(api, db_engine):
    def build(session):
        me, other = make_worker(session), make_worker(session)
        rows = [notify(session, me, minutes=m) for m in (0, 5, 5, 5, 10)]
        notify(session, other, minutes=20)
        return me, rows
    me, rows = seed(db_engine, build)
    login(api, me.id)
    body = api.get(LIST).json()
    expected = sorted(rows, key=lambda r: (r.created_at, r.id), reverse=True)
    assert [item["id"] for item in body["items"]] == [r.id for r in expected]
    assert body["totalItems"] == 5 and body["unreadCount"] == 5
    item = body["items"][0]
    assert set(item) == {"id", "type", "title", "body", "target", "createdAt", "readAt"}
    assert me.id not in str(body)


def test_unread_filter_and_count_are_independent_of_page_and_filter(api, db_engine):
    def build(session):
        me = make_worker(session)
        read = [notify(session, me, minutes=m, read=True) for m in range(3)]
        unread = [notify(session, me, minutes=10 + m) for m in range(4)]
        return me, read, unread
    me, read, unread = seed(db_engine, build)
    login(api, me.id)
    everything = api.get(LIST, params={"read": "ALL", "size": 2}).json()
    assert everything["totalItems"] == 7 and everything["unreadCount"] == 4 and len(everything["items"]) == 2
    only_unread = api.get(LIST, params={"read": "UNREAD"}).json()
    assert [i["id"] for i in only_unread["items"]] == [r.id for r in reversed(unread)]
    assert only_unread["totalItems"] == 4 and only_unread["unreadCount"] == 4
    assert all(item["readAt"] is None for item in only_unread["items"])
    last_page = api.get(LIST, params={"read": "UNREAD", "page": 1, "size": 3}).json()
    assert [i["id"] for i in last_page["items"]] == [unread[0].id] and last_page["unreadCount"] == 4
    read_items = [i for i in api.get(LIST).json()["items"] if i["readAt"] is not None]
    assert {i["id"] for i in read_items} == {r.id for r in read}


def test_all_read_state_is_an_empty_unread_list(api, db_engine):
    me = seed(db_engine, lambda s: worker_with_one(s, read=True))
    login(api, me.id)
    body = api.get(LIST, params={"read": "UNREAD"}).json()
    assert body["items"] == [] and body["totalItems"] == 0 and body["unreadCount"] == 0
    assert api.get(LIST).json()["totalItems"] == 1


def test_pages_cover_every_row_once_and_past_the_end_is_empty(api, db_engine):
    def build(session):
        me = make_worker(session)
        return me, [notify(session, me, minutes=m % 2) for m in range(5)]  # many equal timestamps
    me, rows = seed(db_engine, build)
    login(api, me.id)
    seen = []
    for page in range(3):
        body = api.get(LIST, params={"page": page, "size": 2}).json()
        assert body["page"] == page and body["size"] == 2 and body["totalItems"] == 5
        seen += [item["id"] for item in body["items"]]
    assert len(seen) == 5 and set(seen) == {r.id for r in rows}
    beyond = api.get(LIST, params={"page": 3, "size": 2}).json()
    assert beyond["items"] == [] and beyond["totalItems"] == 5
    assert len(api.get(LIST, params={"size": 100}).json()["items"]) == 5


@pytest.mark.parametrize("query", [
    "read=unread", "read=BOTH", "read=", "read=ALL&read=UNREAD", "size=0", "size=101", "page=-1",
    "page=abc", "size=1.5",
])
def test_invalid_query_is_422(api, db_engine, query):
    login(api, seed(db_engine, make_worker).id)
    response = api.get(f"{LIST}?{query}")
    assert response.status_code == 422 and response.json()["code"] == "VALIDATION_ERROR"
    assert response.json()["fieldErrors"]


def test_listing_does_not_mark_anything_read(api, db_engine):
    me = seed(db_engine, worker_with_one)
    login(api, me.id)
    api.get(LIST)
    assert api.get(LIST).json()["unreadCount"] == 1


def test_rows_stamped_after_as_of_are_not_listed_yet(api, db_engine):
    def build(session):
        me = make_worker(session)
        notify(session, me)
        make_notification(session, me, created_at=NOW + timedelta(days=36500))
        return me
    login(api, seed(db_engine, build).id)
    body = api.get(LIST).json()
    assert body["totalItems"] == 1 and body["unreadCount"] == 1
    assert all(item["createdAt"] <= body["asOf"] for item in body["items"])


def test_target_snapshot_survives_a_cancelled_invitation(api, db_engine):
    """The list never re-checks or rewrites targets; the target's own API re-checks access."""
    def build(session):
        me = make_worker(session)
        invitation = make_invitation(session, make_store(session))
        notify(session, me, type=NotificationType.STORE_INVITED,
               target=StoreInvitationTarget(invitation_id=invitation.id), event_key=invitation.id,
               body="월계 카페에서 초대했어요")
        invitation.canceled_at = NOW + timedelta(hours=1)
        return me, invitation
    me, invitation = seed(db_engine, build)
    login(api, me.id)
    [item] = api.get(LIST).json()["items"]
    assert item["target"] == {"type": "STORE_INVITATION", "invitationId": invitation.id}
    assert "token" not in str(item).lower()


def test_does_not_leak_another_members_notifications_by_count(api, db_engine):
    def build(session):
        me, other = make_worker(session), make_worker(session)
        for m in range(3):
            notify(session, other, minutes=m)
        return me
    login(api, seed(db_engine, build).id)
    body = api.get(LIST).json()
    assert body["items"] == [] and body["totalItems"] == 0 and body["unreadCount"] == 0


def test_rows_are_listed_by_the_stored_snapshot(api, db_engine):
    row = seed(db_engine, lambda s: notify(s, make_worker(s)))
    login(api, row.recipient_user_id)
    [item] = api.get(LIST).json()["items"]
    assert item["title"] == row.title and item["body"] == row.body and item["type"] == row.event_type
    with Session(db_engine) as session:
        assert session.get(Notification, row.id).read_at is None


# --- POST /api/users/me/notifications/{notificationId}/read -----------------------------------


def read_url(notification_id: str) -> str:
    return f"{LIST}/{notification_id}/read"


def mark(api, member, notification_id, key=None, body=None):
    return api.post(read_url(notification_id), json={} if body is None else body,
                    headers=member.headers(key or str(uuid.uuid4())))


def stored_read_at(db_engine, notification_id):
    with Session(db_engine) as session:
        return session.get(Notification, notification_id).read_at


def test_read_sets_read_at_once_and_keeps_it(api, db_engine):
    row = seed(db_engine, lambda s: notify(s, make_worker(s)))
    member = login(api, row.recipient_user_id)
    first = mark(api, member, row.id)
    assert first.status_code == 200 and "Idempotent-Replayed" not in first.headers
    body = first.json()
    assert body["id"] == row.id and body["readAt"] is not None and body["target"] == row.target_context
    again = mark(api, member, row.id)  # a new key: still 200 with the first readAt
    assert again.status_code == 200 and again.json()["readAt"] == body["readAt"]
    assert stored_read_at(db_engine, row.id).isoformat() == body["readAt"]
    listing = api.get(LIST, params={"read": "UNREAD"}).json()
    assert listing["items"] == [] and listing["unreadCount"] == 0


def test_read_only_touches_the_chosen_card(api, db_engine):
    def build(session):
        me = make_worker(session)
        return notify(session, me), notify(session, me, minutes=1)
    chosen, other = seed(db_engine, build)
    member = login(api, chosen.recipient_user_id)
    assert mark(api, member, chosen.id).status_code == 200
    assert stored_read_at(db_engine, other.id) is None
    assert api.get(LIST).json()["unreadCount"] == 1


def test_already_read_notification_keeps_its_original_read_at(api, db_engine):
    row = seed(db_engine, lambda s: notify(s, make_worker(s), read=True))
    member = login(api, row.recipient_user_id)
    response = mark(api, member, row.id)
    assert response.status_code == 200 and response.json()["readAt"] == row.read_at.isoformat()


def test_same_key_replays_the_first_result(api, db_engine):
    row = seed(db_engine, lambda s: notify(s, make_worker(s)))
    member = login(api, row.recipient_user_id)
    key = str(uuid.uuid4())
    first = mark(api, member, row.id, key)
    replay = mark(api, member, row.id, key.upper())  # keys are case-insensitive UUIDs
    assert replay.status_code == 200 and replay.headers["Idempotent-Replayed"] == "true"
    assert replay.json() == first.json()


def test_same_key_for_another_notification_is_409(api, db_engine):
    def build(session):
        me = make_worker(session)
        return notify(session, me), notify(session, me, minutes=1)
    one, two = seed(db_engine, build)
    member = login(api, one.recipient_user_id)
    key = str(uuid.uuid4())
    assert mark(api, member, one.id, key).status_code == 200
    reused = mark(api, member, two.id, key)
    assert reused.status_code == 409 and reused.json()["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert stored_read_at(db_engine, two.id) is None


def test_someone_elses_or_missing_notification_is_the_same_404(api, db_engine):
    def build(session):
        me, other = make_worker(session), make_user(session, "OWNER")
        return me, notify(session, other)
    me, theirs = seed(db_engine, build)
    member = login(api, me.id)
    for notification_id in (theirs.id, str(uuid.uuid4())):
        response = mark(api, member, notification_id)
        assert response.status_code == 404 and response.json()["code"] == "RESOURCE_NOT_FOUND"
    assert stored_read_at(db_engine, theirs.id) is None
    # the failed attempt released its key: the same key works for a real request later
    key = str(uuid.uuid4())
    assert mark(api, member, theirs.id, key).status_code == 404
    mine = seed(db_engine, lambda s: notify(s, s.get(User, me.id)))
    assert mark(api, member, mine.id, key).status_code == 200


def test_upper_case_id_reaches_the_same_notification(api, db_engine):
    row = seed(db_engine, lambda s: notify(s, make_worker(s)))
    member = login(api, row.recipient_user_id)
    key = str(uuid.uuid4())
    assert mark(api, member, row.id.upper(), key).json()["id"] == row.id
    assert mark(api, member, row.id, key).headers["Idempotent-Replayed"] == "true"


@pytest.mark.parametrize("bad_id", ["not-a-uuid", "1234", "%7B" + "0" * 32 + "%7D", "0" * 32])
def test_malformed_id_is_422(api, db_engine, bad_id):
    member = login(api, seed(db_engine, make_worker).id)
    response = mark(api, member, bad_id)
    assert response.status_code == 422 and response.json()["fieldErrors"][0]["field"] == "notificationId"


@pytest.mark.parametrize("body", [{"read": True}, {"readAt": "2026-10-05T01:00:00Z"}, [], "x"])
def test_body_must_be_the_empty_object(api, db_engine, body):
    row = seed(db_engine, lambda s: notify(s, make_worker(s)))
    member = login(api, row.recipient_user_id)
    assert mark(api, member, row.id, body=body).status_code == 422
    assert stored_read_at(db_engine, row.id) is None


def test_missing_body_or_key_is_422(api, db_engine):
    row = seed(db_engine, lambda s: notify(s, make_worker(s)))
    member = login(api, row.recipient_user_id)
    assert api.post(read_url(row.id), headers=member.headers(str(uuid.uuid4()))).status_code == 422
    assert api.post(read_url(row.id), json={}, headers=member.headers()).status_code == 422
    assert api.post(read_url(row.id), json={}, headers=member.headers("not-a-uuid")).status_code == 422
    assert stored_read_at(db_engine, row.id) is None


def test_read_requires_session_and_csrf(api, db_engine):
    row = seed(db_engine, lambda s: notify(s, make_worker(s)))
    anonymous = api.post(read_url(row.id), json={}, headers={
        "Origin": ORIGIN, "Idempotency-Key": str(uuid.uuid4()),
    })
    assert anonymous.status_code == 401 and anonymous.json()["code"] == "SESSION_EXPIRED"
    member = login(api, row.recipient_user_id)
    headers = member.headers(str(uuid.uuid4()))
    for broken in ({**headers, "X-CSRF-Token": "wrong"}, {**headers, "Origin": "http://evil.test"},
                   {k: v for k, v in headers.items() if k != "X-CSRF-Token"}):
        response = api.post(read_url(row.id), json={}, headers=broken)
        assert response.status_code == 403 and response.json()["code"] == "CSRF_INVALID"
    assert stored_read_at(db_engine, row.id) is None


def test_suspended_member_cannot_read_or_replay(api, db_engine):
    row = seed(db_engine, lambda s: notify(s, make_worker(s)))
    member = login(api, row.recipient_user_id)
    key = str(uuid.uuid4())
    assert mark(api, member, row.id, key).status_code == 200
    seed(db_engine, lambda s: setattr(s.get(User, row.recipient_user_id), "status", "SUSPENDED"))
    response = mark(api, member, row.id, key)
    assert response.status_code == 403 and response.json()["code"] == "ACCOUNT_SUSPENDED"


@pytest.mark.parametrize("same_key", [False, True])
def test_concurrent_reads_set_one_read_at(api, db_engine, monkeypatch, same_key):
    from app import idempotency

    monkeypatch.setattr(idempotency, "POLL_INTERVAL_SECONDS", 0.01)
    row = seed(db_engine, lambda s: notify(s, make_worker(s)))
    member = login(api, row.recipient_user_id)
    shared = str(uuid.uuid4())
    barrier = threading.Barrier(5)
    results = [None] * 5

    def run(index):
        barrier.wait()
        results[index] = mark(api, member, row.id, shared if same_key else None)

    threads = [threading.Thread(target=run, args=(i,)) for i in range(5)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert [r.status_code for r in results] == [200] * 5, [r.text for r in results]
    read_at = stored_read_at(db_engine, row.id)
    assert {r.json()["readAt"] for r in results} == {read_at.isoformat()}
    if same_key:
        assert sum("Idempotent-Replayed" not in r.headers for r in results) == 1


@pytest.mark.parametrize("operation", ["list", "read"])
def test_unexpected_failure_is_a_bare_500_and_changes_nothing(api, db_engine, monkeypatch, operation):
    from app import notification_views

    def fail(*_args, **_kwargs):
        raise RuntimeError("SELECT secret FROM somewhere")

    row = seed(db_engine, lambda s: notify(s, make_worker(s)))
    member = login(api, row.recipient_user_id)
    monkeypatch.setattr(notification_views, "notification_body", fail)
    response = api.get(LIST) if operation == "list" else mark(api, member, row.id)
    assert response.status_code == 500 and response.json()["code"] == "INTERNAL_ERROR"
    assert "secret" not in response.text
    assert stored_read_at(db_engine, row.id) is None  # the read rolled back
