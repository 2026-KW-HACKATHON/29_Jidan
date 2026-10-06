"""Read helpers for other domains (home #117, calendar #117) that need jobs-domain rules.

They encode the same rules as the jobs endpoints, so a home card or calendar never disagrees
with the posting, application and work request pages.
"""
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.orm import Session

from app.db.models import JobApplication, JobPosting, ShiftAssignment, Store, WorkRequest
from app.jobs import common
from app.jobs.applications import tab_condition
from app.jobs.search import open_jobs
from app.jobs.state import LIVE_APPLICATION_STATUSES


@dataclass(frozen=True)
class ConfirmedShift:
    """A live (not withdrawn) confirmation, current or completed, with its Seoul times in UTC."""

    shift: ShiftAssignment
    job: JobPosting
    store: Store
    worker_name: str  # the name submitted with the application
    request_revision: int  # the accepted request's revision; stable while the confirmation lives
    start_at: datetime
    end_at: datetime


def confirmed_shifts(db: Session, start_at: datetime, end_at: datetime, *, worker_id: str | None = None,
                     store_ids: list[str] | None = None) -> list[ConfirmedShift]:
    """Live confirmations whose [startAt, endAt) overlaps [start_at, end_at); touching is not.

    Withdrawn confirmations are excluded; completed shifts stay. Ordered by startAt, then
    shift id. Filter by `worker_id` (worker calendar) and/or `store_ids` (owner calendar;
    pass only APPROVED stores the caller owns).
    """
    if store_ids is not None and not store_ids:
        return []
    # A shift starts on its work date and lasts under 24 hours: one day of slack on each side.
    first = common.seoul_today(start_at) - timedelta(days=1)
    last = common.seoul_today(end_at)
    statement = (
        select(ShiftAssignment, JobPosting, Store, JobApplication.applicant_name, WorkRequest.revision)
        .join(JobPosting, JobPosting.id == ShiftAssignment.job_id)
        .join(Store, Store.id == JobPosting.store_id)
        .join(WorkRequest, WorkRequest.id == ShiftAssignment.work_request_id)
        .join(JobApplication, JobApplication.id == WorkRequest.application_id)
        .where(ShiftAssignment.withdrawn_at.is_(None), JobPosting.work_date >= first,
               JobPosting.work_date <= last)
    )
    if worker_id is not None:
        statement = statement.where(ShiftAssignment.worker_id == worker_id)
    if store_ids is not None:
        statement = statement.where(JobPosting.store_id.in_(store_ids))
    shifts = []
    for shift, job, store, name, revision in db.execute(statement).all():
        times = common.job_times(job)
        if times.start_at < end_at and start_at < times.end_at:
            shifts.append(ConfirmedShift(shift, job, store, name, revision, times.start_at, times.end_at))
    return sorted(shifts, key=lambda s: (s.start_at, s.shift.id))


def pending_application_count(db: Session, worker_id: str, at: datetime) -> int:
    """The worker's "신청 중" count: APPLIED/REQUESTED, same as the PENDING tab."""
    return db.scalar(select(func.count()).select_from(JobApplication)
                     .join(JobPosting, JobPosting.id == JobApplication.job_id)
                     .where(JobApplication.worker_id == worker_id, tab_condition("PENDING", at)))


def recruiting_jobs(db: Session, store_id: str, at: datetime, *, limit: int = 3) -> tuple[int, list[dict]]:
    """(count, newest `limit` postings as `JobPosting` bodies) of a store's RECRUITING tab,
    applicant counts read at `at` (the caller's asOf)."""
    store = db.get(Store, store_id)
    condition = (JobPosting.store_id == store_id, JobPosting.status == "RECRUITING")
    total = db.scalar(select(func.count()).select_from(JobPosting).where(*condition))
    jobs = list(db.scalars(select(JobPosting).where(*condition)
                           .order_by(JobPosting.created_at.desc(), JobPosting.id.desc()).limit(limit)))
    counts = common.applicant_counts(db, [job.id for job in jobs], at)
    return total, [common.job_body(job, store, counts.get(job.id, 0)) for job in jobs]


def _recommendable(worker_id: str, at: datetime):
    mine = exists().where(JobApplication.job_id == JobPosting.id, JobApplication.worker_id == worker_id,
                          JobApplication.status.in_(LIVE_APPLICATION_STATUSES))
    return open_jobs(at).where(~mine)


def recommendation_candidates(db: Session, worker_id: str, at: datetime) -> list[tuple[JobPosting, Store]]:
    """Open postings (searchable, not started, unconfirmed) without the worker's live
    application; a past WITHDRAWN/NOT_SELECTED application does not exclude. Unordered:
    home ranks them by availability match, then startAt, then id."""
    return list(db.execute(_recommendable(worker_id, at)).all())


# Start small for common early matches, then amortize DB round trips with bounded narrow pages.
FIRST_CANDIDATE_BATCH = 200
FOLLOWING_CANDIDATE_BATCH = 2000


@dataclass(frozen=True)
class RecommendationCandidate:
    """Only the fields needed to rank a posting; no ORM entity or descriptive content."""

    id: str
    work_date: date
    start_time: time
    end_time: time
    ends_next_day: bool


def recommendation_candidates_by_start(
    db: Session, worker_id: str, at: datetime,
) -> Iterator[RecommendationCandidate]:
    """Bounded scalar pages in (startAt, id) order, in the caller's snapshot.

    Seoul's fixed UTC offset makes (work_date, start_time, id) the same ordering. A page
    starts with FIRST_CANDIDATE_BATCH rows, then at most FOLLOWING_CANDIDATE_BATCH per page.
    Even a complete no-match scan never loads candidate JobPosting/Store entities.
    """
    statement = _recommendable(worker_id, at).with_only_columns(
        JobPosting.id, JobPosting.work_date, JobPosting.start_time,
        JobPosting.end_time, JobPosting.ends_next_day,
        maintain_column_froms=True,
    ).order_by(JobPosting.work_date, JobPosting.start_time, JobPosting.id)
    last = None
    while True:
        batch_size = FIRST_CANDIDATE_BATCH if last is None else FOLLOWING_CANDIDATE_BATCH
        page = statement
        if last is not None:
            page = page.where(or_(
                JobPosting.work_date > last.work_date,
                and_(JobPosting.work_date == last.work_date, or_(
                    JobPosting.start_time > last.start_time,
                    and_(JobPosting.start_time == last.start_time, JobPosting.id > last.id))),
            ))
        rows = db.execute(page.limit(batch_size)).all()
        for row in rows:
            yield RecommendationCandidate(*row)
        if len(rows) < batch_size:
            return
        last = RecommendationCandidate(*rows[-1])
        del rows  # release this page before fetching the next one


def recommendation_details(
    db: Session, worker_id: str, at: datetime, job_ids: list[str],
) -> dict[str, tuple[JobPosting, Store]]:
    """Load only the chosen postings, with the same eligibility and snapshot as the scan.

    Call it in the scan's transaction under REPEATABLE READ (the default): the filter is applied
    again, so a fresher snapshot (READ COMMITTED, or a commit in between) can drop a scanned ID
    and the caller's lookup by ID would fail."""
    if not job_ids:
        return {}
    return {job.id: (job, store) for job, store in db.execute(
        _recommendable(worker_id, at).where(JobPosting.id.in_(job_ids)),
    )}
