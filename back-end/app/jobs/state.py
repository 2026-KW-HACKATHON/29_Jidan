"""State transitions of postings, applications and work requests.

Lock order for every transition is posting -> work requests -> applications -> worker ->
access grants, so concurrent accept / withdraw / close / apply serialize on the posting row
and never deadlock. A worker's answer first takes the store row in share mode
(`share_store`): the store domain locks the store before anything else (#108), and an
acceptance inserts a grant that needs the store row. Callers start with `begin_transition`,
lock the posting (`lock_job`), then use these helpers.

Rows found by a non-unique condition are locked through `lock_each`, one primary key at a time,
never with FOR UPDATE on the condition itself (see there).
"""
import warnings
from datetime import datetime, timedelta

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from app import notification_events
from app.db.models import (
    ApplicationSelectionEffect,
    JobApplication,
    JobPosting,
    ShiftAssignment,
    Store,
    StoreAccessGrant,
    User,
    WorkRequest,
)
from app.errors import ApiError, ErrorCode
from app.jobs.common import ends_after, job_times

LIVE_APPLICATION_STATUSES = ("APPLIED", "REQUESTED", "CONFIRMED")


class LateTransitionWarning(RuntimeWarning):
    """`begin_transition` ran after a read had already opened the transaction."""


def begin_transition(db: Session) -> None:
    """Open the transition's transaction in READ COMMITTED on MySQL.

    Under REPEATABLE READ the snapshot is taken by the first plain read, which may come
    before the posting lock is granted; plain reads after the lock (counts, filled checks,
    response bodies) would then miss what the previous lock holder committed. READ
    COMMITTED makes every read after the lock current. Call it before any read in the
    transaction (run_idempotent commits right before the handler, so a handler's first
    line qualifies). SQLite serializes writers anyway and has no such level.

    Called after the transaction has started, the level can no longer change (SQLAlchemy only
    warns and keeps REPEATABLE READ): that is a `LateTransitionWarning`, an error under pytest
    (pyproject `filterwarnings`) on every dialect, so the mistake fails tests on SQLite too.
    """
    if db.in_transaction():
        warnings.warn("begin_transition() after the transaction started: isolation unchanged",
                      LateTransitionWarning, stacklevel=2)
        return
    if db.get_bind().dialect.name == "mysql":
        db.connection(execution_options={"isolation_level": "READ COMMITTED"})


def locked(statement: Select) -> Select:
    """FOR UPDATE that also refreshes rows already in the identity map (a plain `db.get`
    before the lock would otherwise keep its stale attribute values)."""
    return statement.with_for_update().execution_options(populate_existing=True)


def lock_each(db: Session, model, found: Select) -> list:
    """Lock the rows whose primary keys `found` selects, one at a time in its order.

    FOR UPDATE on a non-unique condition locks every row MySQL examines, and MySQL picks the
    access path from table statistics that drift as rows come and go. Driving a posting's
    PENDING requests from the work_requests status index, or from a table scan, locked every
    store's PENDING requests and deadlocked transitions of unrelated postings (1213). The plain
    read is current because transitions run in READ COMMITTED (`begin_transition`), and the
    posting lock keeps the set from changing before its rows are locked; callers re-check
    each locked row's state.
    """
    rows = []
    for key in list(db.scalars(found)):
        row = db.scalar(locked(select(model).where(model.id == key)))
        if row is not None:
            rows.append(row)
    return rows


def lock_job(db: Session, store_id: str, job_id: str) -> JobPosting:
    """Lock the posting of this store; another store's posting is the same 404 as none."""
    job = db.scalar(locked(select(JobPosting).where(JobPosting.id == job_id)))
    if job is None or job.store_id != store_id:
        raise ApiError(404, ErrorCode.RESOURCE_NOT_FOUND)
    return job


def share_store(db: Session, store_id: str) -> None:
    """Share-lock the store row before the posting.

    The acceptance inserts a TEMPORARY grant, whose foreign key waits for any store-row lock
    while the worker row is already locked; an invitation acceptance holds the store row and
    waits for the same worker row. Waiting for the store first, before holding anything,
    breaks that cycle. Share mode keeps acceptances of one store concurrent.
    """
    db.scalar(select(Store.id).where(Store.id == store_id).with_for_update(read=True))


def bump(row) -> None:
    row.revision += 1


def pending_requests(db: Session, job_id: str) -> list[WorkRequest]:
    """PENDING request rows of the posting, locked (the posting must already be locked)."""
    found = (
        select(WorkRequest.id)
        .join(JobApplication, JobApplication.id == WorkRequest.application_id)
        .where(JobApplication.job_id == job_id, WorkRequest.status == "PENDING")
        .order_by(WorkRequest.requested_at, WorkRequest.id)
    )
    return [request for request in lock_each(db, WorkRequest, found) if request.status == "PENDING"]


def expire_request(db: Session, request: WorkRequest) -> None:
    """Record a due PENDING request as EXPIRED at its deadline and free its application.

    The deadline, not the time this runs, is the end: a late sweep never extends it. Expiry
    is a projection of time that reads already show, so materializing it changes no
    revision: a client holding the revisions it read can still act on them.
    """
    request.status = "EXPIRED"
    request.ended_at = request.expires_at
    application = db.scalar(locked(
        select(JobApplication).where(JobApplication.id == request.application_id)
    ))
    if application.status == "REQUESTED":
        application.status = "APPLIED"
    # notification: WORK_REQUEST_NO_RESPONSE here, not only in the sweep: every posting write
    # settles due requests first, so the sweep may never see them PENDING. Recorded after the
    # domain rows so the notification's locks come last.
    notification_events.work_request_no_response(db, request)


def settle_expired_requests(db: Session, job_id: str, at: datetime) -> list[WorkRequest]:
    """Expire due PENDING requests; returns the requests still pending (now < expiresAt)."""
    live = []
    for request in pending_requests(db, job_id):
        if request.expires_at <= at:
            expire_request(db, request)
        else:
            live.append(request)
    db.flush()
    return live


def close_job(db: Session, job: JobPosting, at: datetime) -> list[JobApplication]:
    """Manual closure of an unconfirmed RECRUITING posting.

    Current APPLIED/REQUESTED applications end as NOT_SELECTED; WITHDRAWN and earlier
    outcomes are untouched. Returns the applications that were ended.
    """
    if settle_expired_requests(db, job.id, at):
        raise ApiError(409, ErrorCode.WORK_REQUEST_WITHDRAWAL_REQUIRED,
                       "유효 대기 요청을 먼저 철회한 뒤 모집을 마감해 주세요.")
    ended = [application for application in lock_each(db, JobApplication, (
        select(JobApplication.id)
        .where(JobApplication.job_id == job.id, JobApplication.status.in_(("APPLIED", "REQUESTED")))
        .order_by(JobApplication.applied_at, JobApplication.id)
    )) if application.status in ("APPLIED", "REQUESTED")]
    for application in ended:
        application.status = "NOT_SELECTED"
        bump(application)
    job.status = "CLOSED"
    job.closed_at = at
    bump(job)
    db.flush()
    return ended


# --- work requests -------------------------------------------------------------------------

REQUEST_WINDOW = timedelta(hours=1)


def request_deadline(requested_at: datetime, start_at: datetime) -> datetime:
    """min(requested + 1 hour, shift start); from this instant on the request is EXPIRED."""
    return min(requested_at + REQUEST_WINDOW, start_at)


def live_shift(db: Session, job_id: str, *, lock: bool = False) -> ShiftAssignment | None:
    # active_job_id is job_id until withdrawal; the unique key keeps a lock on one row.
    statement = select(ShiftAssignment).where(ShiftAssignment.active_job_id == job_id)
    return db.scalar(locked(statement) if lock else statement)


def send_request(db: Session, job: JobPosting, application: JobApplication, owner_id: str,
                 at: datetime) -> WorkRequest:
    """APPLIED -> REQUESTED with a new PENDING request (the posting is locked and checked)."""
    request = WorkRequest(
        application_id=application.id, requested_by_owner_id=owner_id, status="PENDING",
        requested_at=at, expires_at=request_deadline(at, job_times(job).start_at), revision=1,
    )
    db.add(request)
    application.status = "REQUESTED"
    bump(application)
    bump(job)
    db.flush()
    return request


def cancel_request(db: Session, job: JobPosting, request: WorkRequest, application: JobApplication,
                   at: datetime) -> None:
    """Owner withdrawal of a live PENDING request: CANCELLED, application back to APPLIED."""
    request.status = "CANCELLED"
    request.ended_at = at
    bump(request)
    if application.status == "REQUESTED":
        application.status = "APPLIED"
        bump(application)
    bump(job)
    db.flush()


def decline_request(db: Session, job: JobPosting, request: WorkRequest, application: JobApplication,
                    at: datetime) -> None:
    """Worker declines: DECLINED, application back to APPLIED; the posting stays open."""
    request.status = "DECLINED"
    request.responded_at = at
    request.ended_at = at
    bump(request)
    application.status = "APPLIED"
    bump(application)
    bump(job)
    db.flush()


def lock_worker(db: Session, worker_id: str) -> None:
    """Serialize one worker's acceptances across postings (two posting locks do not)."""
    db.scalar(locked(select(User.id).where(User.id == worker_id)))


def overlapping_shift(db: Session, worker_id: str, job: JobPosting) -> ShiftAssignment | None:
    """The worker's other live confirmation whose [start, end) intersects this posting's.

    Adjacent shifts (one ends exactly when the other starts) do not overlap. Only shifts that
    end after this one starts and start no later than its end date are read, so the cost does
    not grow with the worker's completed history.
    """
    times = job_times(job)
    last_date = job.work_date + timedelta(days=1) if job.ends_next_day else job.work_date
    rows = db.execute(
        select(ShiftAssignment, JobPosting)
        .join(JobPosting, JobPosting.id == ShiftAssignment.job_id)
        .where(ShiftAssignment.worker_id == worker_id, ShiftAssignment.withdrawn_at.is_(None),
               ShiftAssignment.job_id != job.id, ends_after(times.start_at),
               JobPosting.work_date <= last_date)
    ).all()
    for shift, other in rows:
        other_times = job_times(other)
        if other_times.start_at < times.end_at and times.start_at < other_times.end_at:
            return shift
    return None


def accept_request(db: Session, job: JobPosting, request: WorkRequest, application: JobApplication,
                   at: datetime) -> ShiftAssignment:
    """Accept and confirm in one transaction (posting, requests and applications are locked).

    Request ACCEPTED, application CONFIRMED, the other current APPLIED/REQUESTED applications
    NOT_SELECTED (each recorded as an effect of this request so a confirmation withdrawal can
    restore exactly them), other pending requests CANCELLED, a shift assignment and its
    TEMPORARY access [accept time, shift end), and the posting CLOSED at the accept time.
    """
    times = job_times(job)
    for other in pending_requests(db, job.id):
        if other.id != request.id:
            other.status = "CANCELLED"
            other.ended_at = at
            bump(other)
    request.status = "ACCEPTED"
    request.responded_at = at
    request.ended_at = at
    bump(request)
    application.status = "CONFIRMED"
    bump(application)
    others = lock_each(db, JobApplication, (
        select(JobApplication.id)
        .where(JobApplication.job_id == job.id, JobApplication.id != application.id,
               JobApplication.status.in_(("APPLIED", "REQUESTED")))
        .order_by(JobApplication.applied_at, JobApplication.id)
    ))
    for other in (other for other in others if other.status in ("APPLIED", "REQUESTED")):
        previous = other.status
        other.status = "NOT_SELECTED"
        bump(other)
        db.add(ApplicationSelectionEffect(request_id=request.id, application_id=other.id,
                                          previous_status=previous, applied_revision=other.revision))
    shift = ShiftAssignment(job_id=job.id, work_request_id=request.id, worker_id=application.worker_id,
                            confirmed_at=at)
    db.add(shift)
    db.flush()
    db.add(StoreAccessGrant(store_id=job.store_id, worker_id=application.worker_id, assignment_id=shift.id,
                            duty_label=job.title, granted_at=at, valid_until=times.end_at))
    job.status = "CLOSED"
    job.closed_at = at
    bump(job)
    db.flush()
    return shift


def withdraw_confirmation(db: Session, job: JobPosting, request: WorkRequest, at: datetime) -> None:
    """Undo an acceptance before the shift starts and reopen the posting.

    The confirmed application returns to APPLIED, and only applications this acceptance made
    NOT_SELECTED -- still NOT_SELECTED at the revision it left them -- return to APPLIED.
    Requests are never reactivated. Only this confirmation's TEMPORARY access ends; an access
    the owner already revoked keeps its first revokedAt.
    """
    request.status = "CONFIRMATION_WITHDRAWN"
    request.ended_at = at
    bump(request)
    confirmed = db.scalar(locked(select(JobApplication).where(JobApplication.id == request.application_id)))
    if confirmed.status == "CONFIRMED":
        confirmed.status = "APPLIED"
        bump(confirmed)
    effects = list(db.scalars(locked(
        select(ApplicationSelectionEffect).where(ApplicationSelectionEffect.request_id == request.id,
                                                 ApplicationSelectionEffect.restored_at.is_(None))
    )))
    for effect in effects:
        affected = db.scalar(locked(select(JobApplication).where(JobApplication.id == effect.application_id)))
        if affected.status == "NOT_SELECTED" and affected.revision == effect.applied_revision:
            affected.status = "APPLIED"
            bump(affected)
            effect.restored_at = at
    shift = db.scalar(locked(select(ShiftAssignment).where(ShiftAssignment.work_request_id == request.id)))
    shift.withdrawn_at = at
    grant = db.scalar(locked(select(StoreAccessGrant).where(StoreAccessGrant.assignment_id == shift.id)))
    if grant is not None and grant.revoked_at is None:
        grant.revoked_at = at
    job.status = "RECRUITING"
    job.closed_at = None
    bump(job)
    db.flush()


def expire_due_requests(at: datetime, *, limit: int = 100) -> int:
    """Record PENDING requests due at `at` as EXPIRED, one posting per transaction.

    Reads already show them EXPIRED and every write on a posting records it first, so this
    sweep is not needed for correctness; it is the hook for the "no response in time" event.
    Each posting is locked like any other transition, so the sweep cannot race an accept, but
    with SKIP LOCKED: a posting another transaction holds is left for the next run (that
    transaction settles due requests itself anyway) instead of being waited for.
    Returns the number of requests recorded.
    """
    from app.db import session_scope

    with session_scope() as db:
        job_ids = list(dict.fromkeys(db.scalars(
            select(JobApplication.job_id)
            .join(WorkRequest, WorkRequest.application_id == JobApplication.id)
            .where(WorkRequest.status == "PENDING", WorkRequest.expires_at <= at)
            .order_by(WorkRequest.expires_at)
            .limit(limit)
        )))
    expired = 0
    for job_id in job_ids:
        with session_scope() as db:
            begin_transition(db)
            if db.scalar(select(JobPosting.id).where(JobPosting.id == job_id)
                         .with_for_update(skip_locked=True)) is None:
                continue  # held by a transition right now; retried on the next run
            due = [r for r in pending_requests(db, job_id) if r.expires_at <= at]
            for request in due:
                expire_request(db, request)
                # notification: WORK_REQUEST_NO_RESPONSE is recorded by expire_request itself
            expired += len(due)
    return expired
