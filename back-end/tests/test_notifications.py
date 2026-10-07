"""record_notification: typed targets, safe text, event+recipient dedupe, caller-owned transaction."""
import threading
import uuid
from datetime import UTC, date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.db.models import Notification, User
from app.notifications import (
    ALLOWED_TARGETS,
    DEFAULT_TITLES,
    EVENT_KEY_GUIDE,
    JobApplicationTarget,
    ManualTarget,
    NotificationType,
    SensitiveTextError,
    StoreInvitationTarget,
    StoreTarget,
    WorkRequestTarget,
    WorkScheduleTarget,
    notification_body,
    notification_event_key,
    record_notification,
    target_from_api,
)
from tests.factories import NOW, make_notification, make_user, make_worker


def _id() -> str:
    return str(uuid.uuid4())


SAMPLE_TARGETS = {
    JobApplicationTarget: lambda: JobApplicationTarget(application_id=_id(), store_id=_id(), job_id=_id()),
    WorkRequestTarget: lambda: WorkRequestTarget(request_id=_id(), store_id=_id(), job_id=_id()),
    StoreInvitationTarget: lambda: StoreInvitationTarget(invitation_id=_id()),
    StoreTarget: lambda: StoreTarget(store_id=_id()),
    ManualTarget: lambda: ManualTarget(store_id=_id()),
    WorkScheduleTarget: lambda: WorkScheduleTarget(event_id=_id(), store_id=_id(), work_date=date(2026, 10, 10)),
}


def _record(session, recipient, **overrides):
    arguments = {
        "recipient_user_id": recipient.id, "type": NotificationType.STORE_APPROVED,
        "target": StoreTarget(store_id=_id()), "event_key": _id(), "body": "월계 카페가 승인됐어요",
    }
    arguments.update(overrides)
    return record_notification(session, **arguments)


def _committed_worker(db_engine) -> SimpleNamespace:
    """A committed worker as a plain object usable from any session or thread."""
    with Session(db_engine) as setup:
        worker_id = make_worker(setup).id
        setup.commit()
    return SimpleNamespace(id=worker_id)


def _count(session) -> int:
    return session.scalar(select(func.count()).select_from(Notification))


def test_every_type_has_targets_titles_and_an_event_key_guide():
    assert set(ALLOWED_TARGETS) == set(NotificationType) == set(DEFAULT_TITLES) == set(EVENT_KEY_GUIDE)
    assert len(NotificationType) == 12


@pytest.mark.parametrize("kind", list(NotificationType))
def test_each_type_stores_each_allowed_target_as_its_api_snapshot(session, kind):
    worker = make_worker(session)
    for cls in ALLOWED_TARGETS[kind]:
        target = SAMPLE_TARGETS[cls]()
        row = _record(session, worker, type=kind, target=target, event_key=f"e-{cls.__name__}")
        assert row.event_type == kind and row.title == DEFAULT_TITLES[kind]
        assert row.target_kind == target.kind and row.target_id == target.target_id
        assert row.target_context == target.to_api()
        assert row.dedupe_key == f"{kind}:e-{cls.__name__}"
        assert row.read_at is None
        assert target_from_api(row.target_context) == target


@pytest.mark.parametrize("kind", list(NotificationType))
def test_each_type_rejects_targets_it_does_not_allow(session, kind):
    worker = make_worker(session)
    for cls, build in SAMPLE_TARGETS.items():
        if cls in ALLOWED_TARGETS[kind]:
            continue
        with pytest.raises(TypeError):
            _record(session, worker, type=kind, target=build())
    assert _count(session) == 0


def test_withdrawals_open_the_ended_request_not_the_cancelled_schedule():
    for kind in (NotificationType.WORK_REQUEST_WITHDRAWN, NotificationType.WORK_CONFIRMATION_WITHDRAWN):
        assert ALLOWED_TARGETS[kind] == (WorkRequestTarget,)


def test_api_shapes_match_the_openapi_required_fields():
    store, event = _id(), _id()
    assert WorkScheduleTarget(event_id=event, store_id=store, work_date=date(2026, 10, 1)).to_api() == {
        "type": "WORK_SCHEDULE", "eventId": event, "storeId": store, "workDate": "2026-10-01",
    }
    assert StoreInvitationTarget(invitation_id=event).to_api() == {"type": "STORE_INVITATION", "invitationId": event}
    assert set(JobApplicationTarget(application_id=event, store_id=store, job_id=store).to_api()) == {
        "type", "applicationId", "storeId", "jobId",
    }
    assert set(WorkRequestTarget(request_id=event, store_id=store, job_id=store).to_api()) == {
        "type", "requestId", "storeId", "jobId",
    }
    assert ManualTarget(store_id=store).to_api() == {"type": "MANUAL", "storeId": store}


@pytest.mark.parametrize("bad", ["", "not-a-uuid", "0" * 36, None, 123, str(uuid.uuid4()) + " ", "{" + _id() + "}"])
def test_targets_require_canonical_uuids(bad):
    with pytest.raises(ValueError):
        StoreTarget(store_id=bad)
    with pytest.raises(ValueError):
        WorkRequestTarget(request_id=_id(), store_id=_id(), job_id=bad)


def test_target_uuids_are_lower_cased():
    value = _id()
    assert StoreTarget(store_id=value.upper()).store_id == value


@pytest.mark.parametrize("bad", [datetime(2026, 10, 10, tzinfo=UTC), "2026-10-10", None])
def test_work_schedule_needs_a_calendar_date(bad):
    with pytest.raises(ValueError):
        WorkScheduleTarget(event_id=_id(), store_id=_id(), work_date=bad)


@pytest.mark.parametrize("data", [
    None, [], {}, {"type": "STORE"}, {"type": "STORE", "storeId": _id(), "extra": "x"},
    {"type": "URL", "storeId": _id()}, {"type": "WORK_SCHEDULE", "eventId": _id(), "storeId": _id(), "workDate": 20261010},
    {"type": "WORK_SCHEDULE", "eventId": _id(), "storeId": _id(), "workDate": "2026-13-01"},
    {"type": "STORE_INVITATION", "invitationId": _id(), "token": "secret"},
])
def test_target_from_api_rejects_malformed_snapshots(data):
    with pytest.raises((TypeError, ValueError)):
        target_from_api(data)


def test_unknown_type_is_rejected(session):
    with pytest.raises(ValueError):
        _record(session, make_worker(session), type="STORE_DELETED")
    with pytest.raises(ValueError):
        _record(session, make_worker(session), type="store_approved")


def test_title_defaults_and_can_be_overridden(session):
    worker = make_worker(session)
    assert _record(session, worker).title == "매장이 승인됐어요"
    assert _record(session, worker, title="  월계 카페 승인 완료  ").title == "월계 카페 승인 완료"


@pytest.mark.parametrize(("field", "value"), [
    ("title", ""), ("title", " 　 "), ("title", "가" * 101), ("title", "두 줄\n제목"),
    ("body", ""), ("body", "\n\t"), ("body", "가" * 501), ("body", None), ("body", 1),
    ("body", "탭\t문자"), ("body", "null\x00문자"),
])
def test_text_must_be_a_short_visible_string(session, field, value):
    with pytest.raises((TypeError, ValueError)):
        _record(session, make_worker(session), **{field: value})
    assert _count(session) == 0


def test_text_length_limits_are_inclusive(session):
    worker = make_worker(session)
    row = _record(session, worker, title="가" * 100, body="나" * 500)
    assert len(row.title) == 100 and len(row.body) == 500
    assert _record(session, worker, body="첫 줄\n둘째 줄").body == "첫 줄\n둘째 줄"


@pytest.mark.parametrize("value", [
    "https://jidan.example.com/invite?token=abc", "HTTP://evil", "www.example.com 확인", "jidan://open",
    "문의 owner@example.com", "연락처 01012345678", "연락처 010-1234-5678", "매장 02 123 4567",
    "사업자 1234567890", "초대 코드 " + "A" * 43, "ID " + str(uuid.uuid4()),
    # Separator, prefix and full-width variants of the same data.
    "연락처 (010)1234-5678", "연락처 010)1234-5678", "연락처 010_1234_5678", "연락처 +82-10-1234-5678",
    "연락처 ０１０-１２３４-５６７８", "사업자 123-45-67890", "문의 owner＠example.com", "evil.com/invite",
])
def test_text_rejects_urls_tokens_and_personal_data(session, value):
    worker = make_worker(session)
    with pytest.raises(SensitiveTextError):
        _record(session, worker, body=value)
    with pytest.raises(SensitiveTextError):
        _record(session, worker, title=value[:100])


@pytest.mark.parametrize("body", [
    "컴포즈커피 광운대점 10월 10일 09:00–14:00 (2026-10-10)", "10월 1일 00:00–08:00(익일) 근무가 있어요",
    "2026.10.10 09시 대타", "월계 2호점 대표번호 1588-1234", "카페 0910 1200 근무",
])
def test_ordinary_schedule_text_is_allowed(session, body):
    assert _record(session, make_worker(session), body=body).body == body


@pytest.mark.parametrize("key", ["", " ", "a b", "가나", "a/b", "x" * 201, None, 5])
def test_event_key_format(session, key):
    with pytest.raises(ValueError):
        _record(session, make_worker(session), event_key=key)


def test_event_key_helper():
    moment = datetime(2026, 10, 5, 12, 0, 0, 1, tzinfo=timezone(timedelta(hours=9)))
    same = moment.astimezone(UTC)
    assert notification_event_key("a", moment) == notification_event_key("a", same)
    assert notification_event_key("a", moment) == "a:1791169200000001"  # 2026-10-05T03:00:00.000001Z
    assert notification_event_key("v", 3, date(2026, 10, 1)) == "v:3:2026-10-01"
    for bad in ((), (datetime(2026, 1, 1),), (True,), (None,), (1.5,)):  # noqa: DTZ001
        with pytest.raises((TypeError, ValueError)):
            notification_event_key(*bad)


def test_created_at_must_be_aware(session):
    with pytest.raises(ValueError):
        _record(session, make_worker(session), created_at=datetime(2026, 10, 5))  # noqa: DTZ001


def test_same_event_for_the_same_recipient_is_stored_once(session):
    worker = make_worker(session)
    target = StoreTarget(store_id=_id())
    first = _record(session, worker, target=target, event_key="store-1")
    again = _record(session, worker, target=target, event_key="store-1", body="다른 문구로 재시도")
    assert again.id == first.id and again.body == first.body
    assert _count(session) == 1


def test_dedupe_is_per_recipient_and_per_type(session):
    worker, other = make_worker(session), make_worker(session)
    target = StoreInvitationTarget(invitation_id=_id())
    _record(session, worker, type=NotificationType.STORE_INVITED, target=target, event_key="inv")
    _record(session, other, type=NotificationType.STORE_INVITED, target=target, event_key="inv")
    _record(session, worker, type=NotificationType.INVITATION_EXPIRED, target=target, event_key="inv")
    assert _count(session) == 3


def test_reusing_an_event_key_for_another_target_is_a_bug(session):
    worker = make_worker(session)
    _record(session, worker, event_key="same")
    with pytest.raises(ValueError, match="another target"):
        _record(session, worker, event_key="same")  # a different random store
    assert _count(session) == 1


def test_a_duplicate_does_not_break_the_callers_transaction(db_engine):
    worker = _committed_worker(db_engine)
    with Session(db_engine) as session:
        target = StoreTarget(store_id=_id())
        _record(session, worker, target=target, event_key="k")
        session.commit()

        session.get(User, worker.id).name = "바뀐이름"  # the caller's own change in the same transaction
        _record(session, worker, target=target, event_key="k")  # duplicate inside a savepoint
        _record(session, worker, event_key="k2")
        session.commit()
    with Session(db_engine) as check:
        assert check.get(User, worker.id).name == "바뀐이름"
        assert _count(check) == 2


def test_rollback_of_the_callers_transaction_leaves_no_notification(db_engine):
    worker = _committed_worker(db_engine)
    with Session(db_engine) as session:
        _record(session, worker)
        session.rollback()
    with Session(db_engine) as check:
        assert _count(check) == 0


def test_unknown_recipient_fails(session):
    with pytest.raises(IntegrityError):
        _record(session, User(id=_id()))


def test_record_never_commits(db_engine):
    worker = _committed_worker(db_engine)
    with Session(db_engine) as session:
        _record(session, worker)
        with Session(db_engine) as other:
            assert _count(other) == 0  # not visible until the caller commits
        session.commit()


def test_concurrent_recording_of_one_event_creates_one_row(db_engine):
    worker = _committed_worker(db_engine)
    target = WorkScheduleTarget(event_id=_id(), store_id=_id(), work_date=date(2026, 10, 10))
    barrier = threading.Barrier(4)
    ids, errors = [], []

    def run():
        try:
            with Session(db_engine) as session:
                barrier.wait()
                row = _record(
                    session, worker, type=NotificationType.WORK_CONFIRMED, target=target, event_key="shift-1",
                )
                session.commit()
                ids.append(row.id)
        except Exception as error:  # noqa: BLE001 - surfaced by the assertion below
            errors.append(error)

    threads = [threading.Thread(target=run) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    if db_engine.dialect.name == "sqlite":
        # SQLite serializes writers; a writer that loses the race may see "database is locked".
        errors = [e for e in errors if "locked" not in str(e)]
    assert errors == []
    assert len(set(ids)) == 1 and ids
    with Session(db_engine) as check:
        assert _count(check) == 1


def test_notification_body_hides_internal_columns(session):
    worker = make_worker(session)
    row = make_notification(session, worker)
    body = notification_body(row)
    assert set(body) == {"id", "type", "title", "body", "target", "createdAt", "readAt"}
    assert body["readAt"] is None and body["createdAt"] == NOW.isoformat()
    assert worker.id not in str(body) and "event-" not in str(body)


def test_database_rejects_blank_text_and_unknown_values(session):
    worker = make_worker(session)
    row = make_notification(session, worker)
    for column, value in (("title", "   "), ("body", "　"), ("event_type", "OTHER"), ("target_kind", "URL")):
        with pytest.raises(DBAPIError), session.begin_nested():
            session.execute(text(f"UPDATE notifications SET {column} = :v WHERE id = :id"), {"v": value, "id": row.id})


def test_owner_can_receive_notifications_too(session):
    owner = make_user(session, "OWNER")
    row = _record(session, owner, type=NotificationType.NEW_APPLICATION,
                  target=SAMPLE_TARGETS[JobApplicationTarget](), event_key="app-1")
    assert row.recipient_user_id == owner.id


def test_upsert_still_enforces_checks_and_foreign_keys(session):
    """The duplicate-tolerant insert must not hide other violations (unlike INSERT IGNORE)."""
    from app.notifications import _insert_unless_duplicate

    worker = make_worker(session)
    base = {
        "id": _id(), "recipient_user_id": worker.id, "event_type": "STORE_APPROVED", "title": "제목",
        "body": "본문", "target_kind": "STORE", "target_id": _id(), "target_context": {}, "dedupe_key": "x",
        "created_at": NOW,
    }
    for change in ({"title": " "}, {"event_type": "store_approved"}, {"recipient_user_id": _id()}):
        with pytest.raises(DBAPIError), session.begin_nested():
            session.execute(_insert_unless_duplicate(session, {**base, **change}))
    assert _count(session) == 0


def test_pending_rows_of_the_caller_are_flushed_first(session):
    owner = User(google_sub=f"sub-{_id()}", google_email="o@example.com", email_verified=True,
                 role="OWNER", name="점주", phone_number="01000000000")
    session.add(owner)  # not flushed yet: the notification's FK needs it
    owner.id = _id()
    row = _record(session, owner)
    assert row.recipient_user_id == owner.id
