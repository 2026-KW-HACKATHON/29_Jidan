"""Demo data for local / dev demonstrations (#122): `python -m app.demo_seed`.

Running it again first deletes every row that belongs to the demo accounts (and rows other
people created against demo stores, postings or applications), then inserts the same set:
accounts, stores, invitations and postings keep fixed ids, relations are identical and dates are
relative to today in Asia/Seoul. Nothing outside that scope is touched.

Guards: never with `APP_ENV=production`, and only on a database whose name ends with
`_local`, `_dev`, `_demo` or `_test` (`DB_NAME`). Demo accounts have Google `sub`s starting with
`demo-seed:` so nobody can sign in as them through Google; they exist to fill screens.

Shape of the data (store "지단 카페 월계점", APPROVED; "지단 분식 월계점", PENDING):

* workers 김지수 (regular access by an accepted invitation, one confirmed upcoming shift and one
  completed shift), 이민준 (not selected once, applied to an open posting, favorite store),
  박서연 (pending work request), 최도윤 (pending invitation, not selected after a closure)
* postings: open with applicants and a pending request, open without applicants, confirmed
  (closed by acceptance), completed (in the past), closed without a selection

Notifications are recorded with the same `app.notification_events` calls the API transitions make
(approval, invitations, applications, requests, confirmations); those older than a day are read.

The demo cafe also has a published manual (`app.manual_demo_seed`) so the worker manual and AI
Q&A screens have content. Other domains add their reset and insert steps to `EXTRA_RESETS` /
`EXTRA_SEEDS` instead of changing the core here.
"""
import os
import re
import sys
import uuid
from collections.abc import Callable
from datetime import date, datetime, time, timedelta
from types import SimpleNamespace

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.orm import Session

from app import notification_events
from app.auth import hash_token
from app.db import session_scope, utcnow
from app.db.models import (
    ApplicationCareer,
    ApplicationSelectionEffect,
    AuthSession,
    AvailabilityDay,
    AvailabilityRule,
    FavoriteStore,
    IdempotencyRecord,
    InvitationMailOutbox,
    JobApplication,
    JobPosting,
    Notification,
    RegistrationSession,
    ShiftAssignment,
    Store,
    StoreAccessGrant,
    StoreApprovalRequest,
    StoreInvitation,
    User,
    WorkerCareer,
    WorkerProfile,
    WorkRequest,
)
from app.idempotency import subject_id_for
from app.jobs import common
from app.jobs.applications import snapshot_application
from app.jobs.state import accept_request, close_job, send_request
from app.manual_demo_reset import reset_manual_data
from app.manual_demo_seed import seed_published_manual
from app.operator_cli import operator_cli

SUB_PREFIX = "demo-seed:"
SAFE_DATABASE = re.compile(r"_(local|dev|demo|test)$")
NAMESPACE = uuid.UUID("8d3f5a52-6a59-4c5b-9f1e-3c7d0e4a2b10")

# Steps other domains append: reset(db, demo_user_ids, demo_store_ids) runs before the core
# reset; seed(db, ids, now) runs after the core insert with the ids returned by `seed`.
EXTRA_RESETS: list[Callable[[Session, list[str], list[str]], None]] = [reset_manual_data]
EXTRA_SEEDS: list[Callable[[Session, dict[str, str], datetime], None]] = [seed_published_manual]


class UnsafeTarget(RuntimeError):
    """The environment or database is not one the demo data may be written to."""


def demo_id(name: str) -> str:
    return str(uuid.uuid5(NAMESPACE, name))


def check_target() -> None:
    environment = os.getenv("APP_ENV", "local").strip()
    if environment == "production":
        raise UnsafeTarget("APP_ENV=production: demo data is never written to production")
    if environment not in ("local", "dev"):
        raise UnsafeTarget(f"APP_ENV must be local or dev, not {environment!r}")
    name = os.getenv("DB_NAME", "").strip()
    if not SAFE_DATABASE.search(name):
        raise UnsafeTarget("DB_NAME must end with _local, _dev, _demo or _test")


# --- reset ---------------------------------------------------------------------------------


def _ids(db: Session, statement) -> list[str]:
    return list(dict.fromkeys(db.scalars(statement)))


def _starts(column, prefix: str):
    """Exact, case-sensitive prefix match: LIKE would treat `_`/`%` as wildcards and is
    case-insensitive on SQLite."""
    return func.substr(column, 1, len(prefix)) == prefix


def reset(db: Session) -> None:
    """Delete everything that belongs to the demo accounts, children first."""
    delete_accounts(db, SUB_PREFIX)


def delete_accounts(db: Session, sub_prefix: str) -> int:
    """Delete the accounts whose Google `sub` starts with `sub_prefix` and everything that
    belongs to them (also rows others created against their stores, postings or applications),
    children first. Returns the number of accounts. The E2E runner's `--cleanup` uses it too.

    Ids are collected before deleting (MySQL cannot delete from a table it also selects in a
    subquery), so the statements stay simple `IN` lists. tests/test_demo_seed.py fails when a
    new table references users or stores without being handled here.
    """
    if not sub_prefix.endswith(":"):
        raise ValueError("an account prefix ends with ':' so it cannot match real Google subs")
    users = _ids(db, select(User.id).where(_starts(User.google_sub, sub_prefix)))
    # Also subs of registrations never completed: their idempotency records outlive the session.
    subs = _ids(db, select(User.google_sub).where(User.id.in_(users)).union(
        select(RegistrationSession.google_sub).where(
            _starts(RegistrationSession.google_sub, sub_prefix))))
    stores = _ids(db, select(Store.id).where(Store.owner_id.in_(users)))
    for extra in EXTRA_RESETS:
        extra(db, users, stores)
    jobs = _ids(db, select(JobPosting.id).where(or_(JobPosting.store_id.in_(stores),
                                                    JobPosting.created_by_owner_id.in_(users))))
    applications = _ids(db, select(JobApplication.id).where(or_(JobApplication.job_id.in_(jobs),
                                                                JobApplication.worker_id.in_(users))))
    requests = _ids(db, select(WorkRequest.id).where(or_(WorkRequest.application_id.in_(applications),
                                                         WorkRequest.requested_by_owner_id.in_(users))))
    shifts = _ids(db, select(ShiftAssignment.id).where(or_(
        ShiftAssignment.job_id.in_(jobs), ShiftAssignment.work_request_id.in_(requests),
        ShiftAssignment.worker_id.in_(users))))
    invitations = _ids(db, select(StoreInvitation.id).where(or_(
        StoreInvitation.store_id.in_(stores), StoreInvitation.inviter_owner_id.in_(users),
        StoreInvitation.accepted_by_worker_id.in_(users), StoreInvitation.declined_by_worker_id.in_(users))))
    rules = _ids(db, select(AvailabilityRule.id).where(AvailabilityRule.worker_id.in_(users)))
    statements = [
        delete(StoreAccessGrant).where(or_(
            StoreAccessGrant.store_id.in_(stores), StoreAccessGrant.worker_id.in_(users),
            StoreAccessGrant.assignment_id.in_(shifts), StoreAccessGrant.invitation_id.in_(invitations))),
        delete(ShiftAssignment).where(ShiftAssignment.id.in_(shifts)),
        delete(ApplicationSelectionEffect).where(or_(
            ApplicationSelectionEffect.request_id.in_(requests),
            ApplicationSelectionEffect.application_id.in_(applications))),
        delete(WorkRequest).where(WorkRequest.id.in_(requests)),
        delete(ApplicationCareer).where(ApplicationCareer.application_id.in_(applications)),
        delete(JobApplication).where(JobApplication.id.in_(applications)),
        delete(JobPosting).where(JobPosting.id.in_(jobs)),
        delete(InvitationMailOutbox).where(InvitationMailOutbox.invitation_id.in_(invitations)),
        delete(StoreInvitation).where(StoreInvitation.id.in_(invitations)),
        delete(FavoriteStore).where(or_(FavoriteStore.store_id.in_(stores), FavoriteStore.worker_id.in_(users))),
        delete(StoreApprovalRequest).where(StoreApprovalRequest.store_id.in_(stores)),
        delete(Store).where(Store.id.in_(stores)),
        delete(AvailabilityDay).where(AvailabilityDay.rule_id.in_(rules)),
        delete(AvailabilityRule).where(AvailabilityRule.id.in_(rules)),
        delete(WorkerCareer).where(WorkerCareer.worker_id.in_(users)),
        delete(WorkerProfile).where(WorkerProfile.user_id.in_(users)),
        delete(Notification).where(Notification.recipient_user_id.in_(users)),
        delete(AuthSession).where(AuthSession.user_id.in_(users)),
        delete(RegistrationSession).where(_starts(RegistrationSession.google_sub, sub_prefix)),
        delete(IdempotencyRecord).where(IdempotencyRecord.subject_id.in_([_subject(s) for s in subs])),
        delete(User).where(User.id.in_(users)),
    ]
    for statement in statements:
        db.execute(statement)
    db.flush()
    return len(users)


def _subject(google_sub: str) -> str:
    """Idempotency subject of an account (subject_id_for only reads `google_sub`)."""
    return subject_id_for(SimpleNamespace(google_sub=google_sub))


# --- insert --------------------------------------------------------------------------------


WORKERS = {
    "jisu": ("김지수", "jisu.demo@jidan.example", date(2001, 3, 14), "FEMALE", "EXPERIENCED"),
    "minjun": ("이민준", "minjun.demo@jidan.example", date(1999, 8, 2), "MALE", "NEW"),
    "seoyeon": ("박서연", "seoyeon.demo@jidan.example", date(2003, 11, 27), "FEMALE", "EXPERIENCED"),
    "doyoon": ("최도윤", "doyoon.demo@jidan.example", date(2000, 1, 9), "MALE", "NEW"),
}
CAREERS = {
    "jisu": [("CAFE", "음료 제조, 마감 정리", "월계 커피", "2023-03", "2024-12", False),
             ("RESTAURANT", "홀 서빙", None, "2025-01", None, True)],
    "seoyeon": [("CONVENIENCE_STORE", "계산, 진열", "광운 편의점", "2024-05", "2025-08", False)],
}
AVAILABILITY = {
    "jisu": [(["MON", "TUE", "WED", "THU", "FRI"], time(17), time(23), False)],
    "minjun": [(["SAT", "SUN"], time(9), time(18), False), (["WED"], time(9), time(13), False)],
    "seoyeon": [(["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"], time(18), time(2), True)],
    "doyoon": [(["TUE", "THU"], time(10), time(16), False)],
}


def _user(db: Session, key: str, role: str, name: str, email: str, at: datetime) -> User:
    user = User(id=demo_id(f"user:{key}"), google_sub=f"{SUB_PREFIX}{key}", google_email=email,
                email_verified=True, role=role, status="ACTIVE", name=name, phone_number="01000000000",
                created_at=at, updated_at=at)
    db.add(user)
    return user


def _worker(db: Session, key: str, at: datetime) -> User:
    name, email, birth, gender, level = WORKERS[key]
    user = _user(db, key, "WORKER", name, email, at)
    db.flush()
    db.add(WorkerProfile(user_id=user.id, birth_date=birth, gender=gender, experience_level=level))
    db.flush()
    for order, (industry, duties, store_name, start, end, current) in enumerate(CAREERS.get(key, [])):
        db.add(WorkerCareer(id=demo_id(f"career:{key}:{order}"), worker_id=user.id, sort_order=order,
                            industry=industry, duties=duties, store_name=store_name, start_month=start,
                            end_month=end, is_current=current))
    for order, (days, start, end, next_day) in enumerate(AVAILABILITY.get(key, [])):
        rule = AvailabilityRule(id=demo_id(f"availability:{key}:{order}"), worker_id=user.id, sort_order=order,
                                start_time=start, end_time=end, ends_next_day=next_day)
        db.add(rule)
        db.flush()
        db.add_all(AvailabilityDay(id=demo_id(f"availability:{key}:{order}:{day}"), rule_id=rule.id, weekday=day)
                   for day in days)
    db.flush()
    return user


def _store(db: Session, key: str, owner: User, *, name: str, industry: str, brn: str, approved_at,
           at: datetime) -> Store:
    status = "APPROVED" if approved_at is not None else "PENDING"
    store = Store(id=demo_id(f"store:{key}"), owner_id=owner.id, name=name, industry=industry,
                  postal_code="01897", address="서울특별시 노원구 광운로 20", detail_address="1층",
                  business_registration_number=brn, phone_number="029400000", approval_status=status,
                  created_at=at, approved_at=approved_at)
    db.add(store)
    db.flush()
    db.add(StoreApprovalRequest(id=demo_id(f"approval:{key}"), store_id=store.id, status=status,
                                submitted_at=at, approved_at=approved_at))
    db.flush()
    return store


def _job(db: Session, key: str, store: Store, work_date: date, start: time, end: time, *, title: str,
         duty: str, part: str, created_at: datetime, pay: int = 11000, experience: int = 0) -> JobPosting:
    job = JobPosting(
        id=demo_id(f"job:{key}"), store_id=store.id, created_by_owner_id=store.owner_id, title=title,
        duty_description=duty, work_part=part, work_date=work_date, start_time=start, end_time=end,
        ends_next_day=end <= start, headcount=1, min_experience_months=experience, extra_requirements="",
        hourly_wage_krw=pay, payment_timing="WORK_DAY", pay_note="", status="RECRUITING",
        created_at=created_at, revision=1,
    )
    db.add(job)
    db.flush()
    return job


def _apply(db: Session, job: JobPosting, worker: User, at: datetime, introduction: str) -> JobApplication:
    application = snapshot_application(db, job, worker.id, introduction, at)
    job.revision += 1
    notification_events.new_application(db, application, job)
    return application


def _request(db: Session, job: JobPosting, application: JobApplication, at: datetime) -> WorkRequest:
    request = send_request(db, job, application, job.created_by_owner_id, at)
    notification_events.work_request_received(db, request, application, job)
    return request


def _confirm(db: Session, job: JobPosting, application: JobApplication, at: datetime) -> WorkRequest:
    request = _request(db, job, application, at)
    accept_request(db, job, request, application, at + timedelta(minutes=10))
    notification_events.work_confirmed(db, request, application, job)
    return request


def seed(db: Session, now: datetime | None = None) -> dict[str, str]:
    """Insert the demo set (after `reset`). Returns the main ids by name."""
    now = now or utcnow()
    today = common.seoul_today(now)
    owner = _user(db, "owner", "OWNER", "지단 점주", "owner.demo@jidan.example", now - timedelta(days=30))
    workers = {key: _worker(db, key, now - timedelta(days=20)) for key in WORKERS}
    cafe = _store(db, "cafe", owner, name="지단 카페 월계점", industry="CAFE", brn="9990000001",
                  approved_at=now - timedelta(days=28), at=now - timedelta(days=29))
    _store(db, "snack", owner, name="지단 분식 월계점", industry="RESTAURANT", brn="9990000002",
           approved_at=None, at=now - timedelta(days=1))

    # Invitations: 김지수 accepted (regular access), 최도윤 still pending.
    accepted_at = now - timedelta(days=14)
    accepted = StoreInvitation(
        id=demo_id("invitation:jisu"), store_id=cafe.id, inviter_owner_id=owner.id,
        invited_email=workers["jisu"].google_email, token_hash=hash_token(f"spent:{uuid.uuid4()}"),
        created_at=accepted_at - timedelta(hours=2), last_sent_at=accepted_at - timedelta(hours=2),
        expires_at=accepted_at + timedelta(days=6), accepted_by_worker_id=workers["jisu"].id, accepted_at=accepted_at)
    pending = StoreInvitation(
        id=demo_id("invitation:doyoon"), store_id=cafe.id, inviter_owner_id=owner.id,
        invited_email=workers["doyoon"].google_email, token_hash=hash_token(f"unsent:{uuid.uuid4()}"),
        created_at=now - timedelta(hours=3), last_sent_at=now - timedelta(hours=3),
        expires_at=now + timedelta(days=7) - timedelta(hours=3))
    db.add_all([accepted, pending])
    db.flush()
    db.add(StoreAccessGrant(id=demo_id("grant:jisu:regular"), store_id=cafe.id, worker_id=workers["jisu"].id,
                            invitation_id=accepted.id, granted_at=accepted_at))
    db.add(FavoriteStore(worker_id=workers["minjun"].id, store_id=cafe.id, saved_at=now - timedelta(days=5)))
    db.flush()
    notification_events.store_approved(db, cafe, at=cafe.approved_at)
    notification_events.invitation_accepted(db, accepted, cafe)
    notification_events.store_invited(db, pending, cafe)

    # A completed shift in the past (calendar history) and an upcoming confirmed one.
    past = _job(db, "completed", cafe, today - timedelta(days=3), time(18), time(22), title="평일 마감 대타",
                duty="마감 정리와 설거지", part="WEEKDAY_CLOSE", created_at=now - timedelta(days=6))
    past_application = _apply(db, past, workers["jisu"], now - timedelta(days=5), "마감 경험이 많습니다.")
    _confirm(db, past, past_application, now - timedelta(days=5) + timedelta(hours=1))

    confirmed = _job(db, "confirmed", cafe, today + timedelta(days=1), time(18), time(22), title="저녁 홀 대타",
                     duty="홀 서빙과 음료 제조", part="WEEKDAY_CLOSE", created_at=now - timedelta(days=2))
    chosen = _apply(db, confirmed, workers["jisu"], now - timedelta(days=2) + timedelta(hours=1),
                    "평일 저녁 근무 가능합니다.")
    _apply(db, confirmed, workers["minjun"], now - timedelta(days=2) + timedelta(hours=2), "처음이지만 성실합니다.")
    _confirm(db, confirmed, chosen, now - timedelta(days=1))

    # Open postings: one with applicants and a live request, one without applicants.
    open_job = _job(db, "open", cafe, today + timedelta(days=2), time(9), time(14), title="주말 오픈 대타",
                    duty="오픈 준비와 고객 응대", part="WEEKEND_OPEN", created_at=now - timedelta(hours=6), pay=12000)
    _apply(db, open_job, workers["minjun"], now - timedelta(hours=5), "오전 근무 좋아합니다.")
    requested = _apply(db, open_job, workers["seoyeon"], now - timedelta(hours=4), "편의점 계산 경험이 있습니다.")
    _request(db, open_job, requested, now)
    _job(db, "quiet", cafe, today + timedelta(days=4), time(17), time(23), title="심야 전 마감 대타",
         duty="매장 정리", part="OTHER", created_at=now - timedelta(hours=1), experience=3)

    # Closed without anyone selected.
    closed = _job(db, "closed", cafe, today + timedelta(days=5), time(10), time(15), title="평일 낮 대타",
                  duty="재고 정리", part="OTHER", created_at=now - timedelta(days=3))
    _apply(db, closed, workers["doyoon"], now - timedelta(days=3) + timedelta(hours=1), "열심히 하겠습니다.")
    close_job(db, closed, now - timedelta(days=1))

    # What a returning user has already seen: everything older than a day is read.
    db.execute(update(Notification).where(
        Notification.recipient_user_id.in_([owner.id, *(user.id for user in workers.values())]),
        Notification.created_at < now - timedelta(days=1)).values(read_at=now))

    ids = {"owner": owner.id, "cafe": cafe.id, "snack": demo_id("store:snack"), "confirmed_job": confirmed.id,
           "open_job": open_job.id,
           **{f"worker:{key}": user.id for key, user in workers.items()}}
    for extra in EXTRA_SEEDS:
        extra(db, ids, now)
    db.flush()
    return ids


def run(now: datetime | None = None) -> dict[str, str]:
    """The operator entry point (`main`, tests): refuses unsafe targets, then resets and seeds."""
    check_target()
    with operator_cli(), session_scope() as db:
        reset(db)
        return seed(db, now)


def main() -> int:
    try:
        ids = run()
    except UnsafeTarget as error:
        print(f"refused: {error}", file=sys.stderr)
        return 2
    print("demo data ready:")
    for name in ("owner", "cafe", "snack", "confirmed_job", "open_job"):
        print(f"  {name}: {ids[name]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
