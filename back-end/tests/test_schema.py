"""Database-level guarantees of the baseline schema (SQLite with foreign keys enforced)."""

from datetime import date, time, timedelta

import pytest
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.db.models import (
    ApplicationCareer,
    ApplicationSelectionEffect,
    AvailabilityDay,
    AvailabilityRule,
    StoreAccessGrant,
    StoreApprovalRequest,
    WorkerCareer,
    WorkRequest,
)
from tests.factories import (
    NOW,
    make_application,
    make_invitation,
    make_job,
    make_request,
    make_shift,
    make_store,
    make_user,
    make_worker,
)


def rejected(session, action, also=()):
    """The statement must violate a constraint; the savepoint keeps earlier rows intact.

    `also` lists extra MySQL error codes that count, e.g. 1406 (value longer than the column)."""
    with pytest.raises(DBAPIError) as caught, session.begin_nested():
        action()
        session.flush()
    # MySQL reports a violated CHECK (errno 3819) as OperationalError, not IntegrityError.
    assert isinstance(caught.value, IntegrityError) or caught.value.orig.args[0] in (3819, *also)


def test_users_unique_google_sub_and_enum_checks(session):
    first = make_user(session)
    rejected(session, lambda: make_user(session, google_sub=first.google_sub))
    rejected(session, lambda: make_user(session, role="ADMIN"))
    rejected(session, lambda: make_user(session, status="DELETED"))


def test_foreign_keys_are_enforced(session):
    rejected(session, lambda: make_store(session, owner=type("U", (), {"id": "missing"})()))


def test_store_approval_status_must_match_timestamp(session):
    rejected(session, lambda: make_store(session, approval_status="APPROVED"))
    rejected(session, lambda: make_store(session, approved_at=NOW))
    make_store(session, approval_status="APPROVED", approved_at=NOW)


def test_business_registration_number_unique_and_ten_digits(session):
    store = make_store(session)
    rejected(session, lambda: make_store(
        session, business_registration_number=store.business_registration_number))
    rejected(session, lambda: make_store(session, business_registration_number="123456789"))


@pytest.mark.parametrize("value", [
    "가나다a",  # 4 characters but 10 bytes: LENGTH counts bytes on MySQL
    "123456789",
    "12345678901",
    "12345abcde",
    "123-45-6789",
    "12345 6789",
    " 123456789",
    "123456789 ",
    "123456789\n",
    "１２３４５６７８９０",  # full-width digits: 10 characters, 30 bytes
    "",
], ids=["hangul-10-bytes", "9-digits", "11-digits", "letters", "hyphens", "inner-space",
        "leading-space", "trailing-space", "trailing-newline", "full-width-digits", "empty"])
def test_business_registration_number_rejects_anything_but_ten_ascii_digits(session, value):
    # Longer than the VARCHAR(10) column: MySQL refuses it before the CHECK (1406), SQLite has no limit.
    rejected(session, lambda: make_store(session, business_registration_number=value), also=(1406,))


@pytest.mark.parametrize("value", ["0123456789", "0000000000", "1234567890"])
def test_business_registration_number_accepts_ten_digits_including_leading_zero(session, value):
    store = make_store(session, business_registration_number=value)
    session.expire_all()
    assert store.business_registration_number == value


def test_one_approval_request_per_store(session):
    store = make_store(session)
    session.add(StoreApprovalRequest(store_id=store.id))
    session.flush()
    rejected(session, lambda: session.add(StoreApprovalRequest(store_id=store.id)))
    other = make_store(session)
    rejected(session, lambda: session.add(
        StoreApprovalRequest(store_id=other.id, status="APPROVED")))


def test_worker_career_current_and_end_month_rules(session):
    worker = make_worker(session)

    def career(order, **kwargs):
        values = {"worker_id": worker.id, "sort_order": order, "industry": "CAFE", "duties": "서빙",
                  "store_name": "A", "start_month": "2024-01", "end_month": None,
                  "is_current": True}
        session.add(WorkerCareer(**{**values, **kwargs}))

    career(0)
    rejected(session, lambda: career(1, end_month="2025-01"))  # current but has an end
    rejected(session, lambda: career(1, is_current=False))  # ended but no end month
    rejected(session, lambda: career(1, is_current=False, end_month="2023-12"))  # reversed
    rejected(session, lambda: career(0, is_current=False, end_month="2025-01"))  # duplicate order
    career(1, is_current=False, end_month="2024-01")  # same month is allowed


def test_availability_rule_span_and_days(session):
    worker = make_worker(session)

    def rule(order, start, end, next_day):
        rule = AvailabilityRule(worker_id=worker.id, sort_order=order, start_time=start,
                                end_time=end, ends_next_day=next_day)
        session.add(rule)
        return rule

    rule(0, time(9), time(18), False)
    rule(1, time(22), time(2), True)  # overnight
    rule(2, time(9), time(9), True)  # exactly 24 hours
    rejected(session, lambda: rule(3, time(9), time(9), False))  # zero length
    rejected(session, lambda: rule(3, time(18), time(9), False))  # backwards without next day
    rejected(session, lambda: rule(3, time(9), time(18), True))  # next day but longer than 24h
    saved = rule(3, time(1), time(2), False)
    session.flush()
    session.add(AvailabilityDay(rule_id=saved.id, weekday="MON"))
    session.flush()
    rejected(session, lambda: session.add(AvailabilityDay(rule_id=saved.id, weekday="MON")))
    rejected(session, lambda: session.add(AvailabilityDay(rule_id=saved.id, weekday="XXX")))


def test_invitation_rules(session):
    store = make_store(session)
    worker = make_worker(session)
    make_invitation(session, store)
    rejected(session, lambda: make_invitation(session, store, expires_at=NOW))
    rejected(session, lambda: make_invitation(session, store, access_expires_at=NOW))
    make_invitation(session, store, access_expires_at=NOW + timedelta(days=30))
    # outcomes are mutually exclusive and carry their actor
    rejected(session, lambda: make_invitation(
        session, store, accepted_at=NOW, accepted_by_worker_id=worker.id, canceled_at=NOW))
    rejected(session, lambda: make_invitation(session, store, accepted_at=NOW))
    rejected(session, lambda: make_invitation(session, store, declined_by_worker_id=worker.id))
    make_invitation(session, store, declined_at=NOW, declined_by_worker_id=worker.id)
    make_invitation(session, store, canceled_at=NOW)


def test_invitation_token_hash_is_unique(session):
    store = make_store(session)
    first = make_invitation(session, store)
    rejected(session, lambda: make_invitation(session, store, token_hash=first.token_hash))


def _grant_prerequisites(session):
    store = make_store(session)
    worker = make_worker(session)
    return store, worker, make_invitation(session, store)


def test_access_grant_requires_exactly_one_source(session):
    store, worker, invitation = _grant_prerequisites(session)
    job = make_job(session, store)
    request = make_request(session, make_application(session, job, worker), store.owner_id)
    shift = make_shift(session, job, request, worker.id)

    def grant(**source):
        session.add(StoreAccessGrant(store_id=store.id, worker_id=worker.id, granted_at=NOW, **source))

    rejected(session, lambda: grant())  # neither
    rejected(session, lambda: grant(invitation_id=invitation.id, assignment_id=shift.id))  # both
    grant(invitation_id=invitation.id)
    session.flush()
    rejected(session, lambda: grant(invitation_id=invitation.id))  # one grant per invitation
    grant(assignment_id=shift.id)
    session.flush()
    rejected(session, lambda: grant(assignment_id=shift.id))


def test_access_grant_validity_window(session):
    store, worker, invitation = _grant_prerequisites(session)
    rejected(session, lambda: session.add(StoreAccessGrant(
        store_id=store.id, worker_id=worker.id, invitation_id=invitation.id, granted_at=NOW,
        valid_until=NOW)))
    rejected(session, lambda: session.add(StoreAccessGrant(
        store_id=store.id, worker_id=worker.id, invitation_id=invitation.id, granted_at=NOW,
        revoked_at=NOW - timedelta(seconds=1))))


def test_job_posting_rules(session):
    store = make_store(session)
    make_job(session, store)
    rejected(session, lambda: make_job(session, store, status="CLOSED"))  # closed needs closed_at
    rejected(session, lambda: make_job(session, store, closed_at=NOW))  # open must not be closed
    make_job(session, store, status="CLOSED", closed_at=NOW)
    rejected(session, lambda: make_job(session, store, headcount=2))
    rejected(session, lambda: make_job(session, store, min_experience_months=5))
    rejected(session, lambda: make_job(session, store, hourly_wage_krw=0))
    rejected(session, lambda: make_job(session, store, revision=0))
    rejected(session, lambda: make_job(session, store, payment_timing="LATER"))
    rejected(session, lambda: make_job(session, store, work_part="NIGHT"))
    rejected(session, lambda: make_job(session, store, end_time=time(17), ends_next_day=False))
    make_job(session, store, start_time=time(22), end_time=time(2), ends_next_day=True)


def test_only_one_live_application_per_worker_and_job(session):
    job = make_job(session)
    worker = make_worker(session)
    first = make_application(session, job, worker)
    rejected(session, lambda: make_application(session, job, worker))
    # a different worker, or the same worker on another job, is unaffected
    make_application(session, job, make_worker(session))
    make_application(session, make_job(session), worker)
    # after withdrawal the worker may re-apply and the history is kept
    first.status = "WITHDRAWN"
    first.withdrawn_at = NOW
    session.flush()
    again = make_application(session, job, worker)
    assert again.id != first.id and again.active_worker_id == worker.id
    session.refresh(first)
    assert first.active_worker_id is None


@pytest.mark.parametrize("terminal", ["WITHDRAWN", "NOT_SELECTED", "COMPLETED"])
def test_ended_applications_do_not_block_new_ones(session, terminal):
    job = make_job(session)
    worker = make_worker(session)
    extra = {"withdrawn_at": NOW} if terminal == "WITHDRAWN" else {}
    make_application(session, job, worker, status=terminal, **extra)
    make_application(session, job, worker, status=terminal, **extra)
    make_application(session, job, worker)


@pytest.mark.parametrize("live", ["APPLIED", "REQUESTED", "CONFIRMED"])
def test_each_live_status_is_exclusive(session, live):
    job = make_job(session)
    worker = make_worker(session)
    make_application(session, job, worker, status=live)
    rejected(session, lambda: make_application(session, job, worker))


def test_application_content_rules(session):
    job = make_job(session)
    rejected(session, lambda: make_application(session, job, introduction="   "))
    rejected(session, lambda: make_application(session, job, status="WITHDRAWN"))  # no timestamp
    rejected(session, lambda: make_application(session, job, withdrawn_at=NOW))  # timestamp but live
    rejected(session, lambda: make_application(session, job, status="MAYBE"))
    rejected(session, lambda: make_application(session, job, age_at_submission=-1))


# Whitespace-only values must be rejected identically by SQLite and MySQL: tab, newline, vertical
# tab, form feed, carriage return, NBSP, NEL, en/em space, line/paragraph separator, ideographic
# space. Invisible non-whitespace (zero-width space, control characters) is content on both.
BLANKS = ["", " ", "  ", "\t", "\n", "\r\n", "\x0b", "\x0c", "\u00a0", "\u0085", "\u2003",
          "\u2028", "\u2029", "\u3000", " \t\n\u3000 "]
INVISIBLE_CONTENT = ["\u200b", "\x01", "\ufeff"]


@pytest.mark.parametrize("value", BLANKS, ids=repr)
def test_blank_introduction_is_rejected_in_every_whitespace_form(session, value):
    rejected(session, lambda: make_application(session, make_job(session), introduction=value))


@pytest.mark.parametrize("value", ["a", " a ", "\ta\n", "가", *INVISIBLE_CONTENT], ids=repr)
def test_introduction_with_any_non_whitespace_character_is_accepted(session, value):
    application = make_application(session, make_job(session), introduction=value)
    session.expire_all()
    assert application.introduction == value


@pytest.mark.parametrize("table", [WorkerCareer.__table__, ApplicationCareer.__table__])
def test_store_name_check_treats_whitespace_forms_alike_on_both_databases(session, table):
    if table.name == "worker_careers":
        owner = {"worker_id": make_worker(session).id}
    else:
        owner = {"application_id": make_application(session, make_job(session, make_store(session))).id}
    values = {**owner, "sort_order": 0, "industry": "CAFE", "duties": "서빙",
              "start_month": "2024-01", "end_month": None, "is_current": True}
    for n, blank in enumerate(BLANKS):
        rejected(session, lambda n=n, blank=blank: session.execute(table.insert().values(
            **values, id=f"blank-{n}", store_name=blank)))
    for n, content in enumerate(["a", " a ", *INVISIBLE_CONTENT]):
        session.execute(table.insert().values(
            **{**values, "sort_order": n + 1}, id=f"ok-{n}", store_name=content))


def test_work_request_rules(session):
    job = make_job(session)
    application = make_application(session, job)
    owner_id = job.created_by_owner_id
    make_request(session, application, owner_id)
    rejected(session, lambda: make_request(session, application, owner_id))  # second PENDING
    make_request(session, application, owner_id, status="EXPIRED", ended_at=NOW)
    make_request(session, application, owner_id, status="CANCELLED", ended_at=NOW)
    rejected(session, lambda: make_request(session, application, owner_id, status="EXPIRED"))
    rejected(session, lambda: make_request(
        session, application, owner_id, status="ACCEPTED", ended_at=NOW))  # needs responded_at
    rejected(session, lambda: make_request(session, application, owner_id, ended_at=NOW))
    rejected(session, lambda: make_request(session, application, owner_id, expires_at=NOW))
    make_request(session, application, owner_id, status="ACCEPTED", ended_at=NOW, responded_at=NOW)


def _accepted_shift(session, job, application, withdrawn_at=None):
    request = make_request(
        session, application, job.created_by_owner_id, status="ACCEPTED", ended_at=NOW,
        responded_at=NOW)
    return make_shift(session, job, request, application.worker_id, withdrawn_at=withdrawn_at)


def test_one_live_shift_per_job_but_history_is_kept(session):
    job = make_job(session)
    first_application = make_application(session, job)
    shift = _accepted_shift(session, job, first_application)
    rejected(session, lambda: _accepted_shift(session, job, make_application(session, job)))
    shift.withdrawn_at = NOW + timedelta(hours=1)
    session.flush()
    again = _accepted_shift(session, job, make_application(session, job))
    assert again.id != shift.id and again.active_job_id == job.id


def test_shift_withdrawal_cannot_precede_confirmation(session):
    job = make_job(session)
    rejected(session, lambda: _accepted_shift(
        session, job, make_application(session, job), withdrawn_at=NOW - timedelta(hours=1)))


def test_work_request_confirms_at_most_one_shift(session):
    job = make_job(session)
    shift = _accepted_shift(session, job, make_application(session, job))
    request = session.get(WorkRequest, shift.work_request_id)
    rejected(session, lambda: make_shift(session, job, request, shift.worker_id))


def test_selection_effect_key_and_status(session):
    job = make_job(session)
    application = make_application(session, job)
    request = make_request(session, application, job.created_by_owner_id)
    effect = {"request_id": request.id, "application_id": application.id,
              "previous_status": "APPLIED", "applied_revision": 2}
    session.add(ApplicationSelectionEffect(**effect))
    session.flush()
    rejected(session, lambda: session.add(ApplicationSelectionEffect(**effect)))
    rejected(session, lambda: session.add(ApplicationSelectionEffect(
        **{**effect, "application_id": make_application(session, job).id, "previous_status": "?"})))


def test_parents_with_history_cannot_be_deleted(session):
    job = make_job(session)
    make_application(session, job)
    rejected(session, lambda: session.delete(job))


def test_dates_are_plain_calendar_values(session):
    job = make_job(session, work_date=date(2026, 12, 31))
    session.expire_all()
    assert session.get(type(job), job.id).work_date == date(2026, 12, 31)


def career_values(owner_key, owner_id, **overrides):
    return {owner_key: owner_id, "sort_order": 0, "industry": "CAFE", "duties": "서빙",
            "start_month": "2024-01", "end_month": None, "is_current": True, **overrides}


def test_worker_career_store_name_is_optional_and_normalized(session):
    worker = make_worker(session)
    omitted = WorkerCareer(**career_values("worker_id", worker.id, sort_order=0))
    padded = WorkerCareer(**career_values("worker_id", worker.id, sort_order=1,
                                          store_name="  월계 카페 "))
    empty = WorkerCareer(**career_values("worker_id", worker.id, sort_order=2, store_name=""))
    blank = WorkerCareer(**career_values("worker_id", worker.id, sort_order=3, store_name=" \t "))
    session.add_all([omitted, padded, empty, blank])
    session.flush()
    session.expire_all()
    assert [c.store_name for c in (omitted, padded, empty, blank)] == [None, "월계 카페", None, None]


def test_application_career_snapshot_store_name_is_optional_and_normalized(session):
    application = make_application(session, make_job(session, make_store(session)))
    rows = [
        ApplicationCareer(**career_values("application_id", application.id, sort_order=0)),
        ApplicationCareer(**career_values("application_id", application.id, sort_order=1,
                                          store_name=" 명랑핫도그 ")),
        ApplicationCareer(**career_values("application_id", application.id, sort_order=2,
                                          store_name="   ")),
    ]
    session.add_all(rows)
    session.flush()
    session.expire_all()
    assert [r.store_name for r in rows] == [None, "명랑핫도그", None]


@pytest.mark.parametrize("table", [WorkerCareer.__table__, ApplicationCareer.__table__])
def test_blank_store_name_is_rejected_when_the_orm_is_bypassed(session, table):
    """The CHECK is the last line of defense for writers that skip the ORM normalization."""
    if table.name == "worker_careers":
        owner = {"worker_id": make_worker(session).id}
    else:
        owner = {"application_id": make_application(session, make_job(session, make_store(session))).id}
    values = {**owner, "id": "c-1", "sort_order": 0, "industry": "CAFE", "duties": "서빙",
              "start_month": "2024-01", "end_month": None, "is_current": True}
    rejected(session, lambda: session.execute(table.insert().values(**values, store_name="  ")))
    rejected(session, lambda: session.execute(table.insert().values(**values, store_name="")))
    session.execute(table.insert().values(**values, store_name=None))


def test_normalize_optional_text():
    from app.db.types import normalize_optional_text

    assert normalize_optional_text(None) is None
    assert normalize_optional_text("") is None
    assert normalize_optional_text("  ") is None
    assert normalize_optional_text("\u3000") is None  # full-width space
    assert normalize_optional_text(" a b ") == "a b"
