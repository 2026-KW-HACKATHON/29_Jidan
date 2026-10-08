"""Worker job search and detail with the caller's own application state (#111).

Search returns RECRUITING postings of APPROVED stores that have not started yet. The detail
page also opens closed or started postings (old links) but then answers canApply=false.
"""
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.auth import CurrentWorker
from app.db import SessionDep
from app.db.models import JobApplication, JobPosting, ShiftAssignment, Store
from app.errors import ApiError, ErrorCode
from app.jobs import common
from app.jobs.state import LIVE_APPLICATION_STATUSES
from app.pagination import Pagination

router = APIRouter()

INDUSTRIES = ("RESTAURANT", "CAFE", "CONVENIENCE_STORE", "OTHER")
WORK_DAYS = {"ALL": None, "TODAY": (0, 1), "TOMORROW": (1, 2), "WEEK": (0, 7), "MONTH": (0, 30)}
TIME_BANDS = ("ALL", "MORNING", "AFTERNOON", "NIGHT")
MAX_QUERY_LENGTH = 100

SIX, NOON, EIGHTEEN, MIDNIGHT = time(6), time(12), time(18), time(0)


# --- query building blocks (also used by home recommendations) ------------------------------


def open_jobs(at: datetime):
    """Postings a worker can discover: RECRUITING, APPROVED store, start still ahead.

    A RECRUITING posting never has a live confirmation (acceptance closes it), so this is
    also "unconfirmed".
    """
    return (
        select(JobPosting, Store)
        .join(Store, Store.id == JobPosting.store_id)
        .where(Store.approval_status == "APPROVED", JobPosting.status == "RECRUITING",
               common.starts_after(at))
    )


def _band_overlap(band_start: time, band_end: time):
    """Shift [start, end) meets the daily band [band_start, band_end) (touching is not)."""
    same_day = and_(JobPosting.start_time < band_end, JobPosting.end_time > band_start)
    # An overnight shift is [start, 24:00) on day 0 plus [00:00, end) on day 1.
    overnight = or_(JobPosting.start_time < band_end, JobPosting.end_time > band_start)
    return or_(and_(JobPosting.ends_next_day.is_(False), same_day),
               and_(JobPosting.ends_next_day.is_(True), overnight))


def time_band(band: str):
    if band == "MORNING":
        return _band_overlap(SIX, NOON)
    if band == "AFTERNOON":
        return _band_overlap(NOON, EIGHTEEN)
    # NIGHT is [18:00, next 06:00): every overnight shift crosses midnight and so meets it.
    return or_(JobPosting.ends_next_day.is_(True),
               JobPosting.start_time < SIX, JobPosting.end_time > EIGHTEEN)


LIKE_ESCAPE = "/"


def escape_like(value: str) -> str:
    """Match `%`, `_` and the escape character itself literally."""
    return value.replace("/", "//").replace("%", "/%").replace("_", "/_")


# --- applicability -------------------------------------------------------------------------


def live_applications(db: Session, worker_id: str, job_ids: list[str]) -> dict[str, JobApplication]:
    """The caller's current APPLIED/REQUESTED/CONFIRMED application per posting (at most one)."""
    if not job_ids:
        return {}
    rows = db.scalars(select(JobApplication).where(
        JobApplication.worker_id == worker_id, JobApplication.job_id.in_(job_ids),
        JobApplication.status.in_(LIVE_APPLICATION_STATUSES),
    ))
    return {row.job_id: row for row in rows}


def filled_jobs(db: Session, job_ids: list[str]) -> set[str]:
    """Postings with a live (not withdrawn) confirmation."""
    if not job_ids:
        return set()
    return set(db.scalars(select(ShiftAssignment.job_id).where(
        ShiftAssignment.job_id.in_(job_ids), ShiftAssignment.withdrawn_at.is_(None),
    )))


def cannot_apply_reason(job: JobPosting, mine: JobApplication | None, filled: bool,
                        at: datetime) -> str | None:
    """Why the caller cannot apply now, or None. The caller's own live application wins,
    then a filled posting (before the general CLOSED), then closed, then started."""
    if mine is not None:
        return "ALREADY_CONFIRMED" if mine.status == "CONFIRMED" else "ALREADY_APPLIED"
    if filled:
        return "JOB_FILLED"
    if job.status != "RECRUITING":
        return "JOB_CLOSED"
    if common.job_times(job).start_at <= at:
        return "JOB_STARTED"
    return None


def worker_job_body(job: JobPosting, store: Store, applicant_count: int,
                    mine: JobApplication | None, filled: bool, at: datetime) -> dict:
    """The `WorkerJobPosting` schema."""
    reason = cannot_apply_reason(job, mine, filled, at)
    return {
        **common.job_body(job, store, applicant_count),
        "myApplicationId": mine.id if mine is not None else None,
        "canApply": reason is None,
        "cannotApplyReason": reason,
    }


def worker_job_bodies(db: Session, worker_id: str, rows: list[tuple[JobPosting, Store]],
                      at: datetime) -> list[dict]:
    ids = [job.id for job, _store in rows]
    counts = common.applicant_counts(db, ids, at)
    mine = live_applications(db, worker_id, ids)
    filled = filled_jobs(db, ids)

    def current(job: JobPosting) -> JobApplication | None:
        # An open application of a started shift reads NOT_SELECTED: no longer the current one.
        application = mine.get(job.id)
        if application is None or common.unselected_at_start(application.status, job, at):
            return None
        return application

    return [worker_job_body(job, store, counts.get(job.id, 0), current(job), job.id in filled, at)
            for job, store in rows]


# --- query parameters ----------------------------------------------------------------------


@dataclass(frozen=True)
class SearchFilters:
    q: str
    industry: str | None
    work_day: str
    band: str


def _single(request: Request, name: str, errors: list) -> str | None:
    values = request.query_params.getlist(name)
    if len(values) > 1:
        errors.append({"field": name, "code": "INVALID_FORMAT", "message": "한 번만 입력해 주세요."})
        return None
    return values[0] if values else None


def search_filters(request: Request) -> SearchFilters:
    """`q`, `industry`, `workDay`, `timeBand`; every invalid parameter is reported at once."""
    errors: list = []

    def choice(name: str, allowed, default):
        value = _single(request, name, errors)
        if value is None:
            return default
        if value not in allowed:
            errors.append({"field": name, "code": "INVALID_FORMAT", "message": "허용된 값이 아닙니다."})
        return value

    q = _single(request, "q", errors) or ""
    if len(q) > MAX_QUERY_LENGTH:
        errors.append({"field": "q", "code": "OUT_OF_RANGE", "message": "100자 이하로 입력해 주세요."})
    industry = choice("industry", INDUSTRIES, None)
    work_day = choice("workDay", WORK_DAYS, "ALL")
    band = choice("timeBand", TIME_BANDS, "ALL")
    if errors:
        raise ApiError(422, ErrorCode.VALIDATION_ERROR, field_errors=errors)
    return SearchFilters(q.strip(), industry, work_day, band)


# --- endpoints -----------------------------------------------------------------------------


@router.get("/api/job-postings")
def search_job_postings(worker: CurrentWorker, db: SessionDep, page: Pagination,
                        filters: Annotated[SearchFilters, Depends(search_filters)]):
    at = common.now()
    statement = open_jobs(at)
    if filters.q:
        pattern = f"%{escape_like(filters.q)}%"
        statement = statement.where(or_(
            Store.name.like(pattern, escape=LIKE_ESCAPE), JobPosting.title.like(pattern, escape=LIKE_ESCAPE),
            JobPosting.duty_description.like(pattern, escape=LIKE_ESCAPE),
        ))
    if filters.industry is not None:
        statement = statement.where(Store.industry == filters.industry)
    if WORK_DAYS[filters.work_day] is not None:
        first, last = WORK_DAYS[filters.work_day]
        today = common.seoul_today(at)
        statement = statement.where(JobPosting.work_date >= today + timedelta(days=first),
                                    JobPosting.work_date < today + timedelta(days=last))
    if filters.band != "ALL":
        statement = statement.where(time_band(filters.band))
    total = db.scalar(select(func.count()).select_from(statement.subquery()))
    rows = list(db.execute(
        statement.order_by(JobPosting.created_at.desc(), JobPosting.id.desc())
        .offset(page.offset).limit(page.limit)
    ).all())
    return {
        "items": worker_job_bodies(db, worker.user_id, rows, at),
        "page": page.page, "size": page.size, "totalItems": total, "asOf": common.iso(at),
    }


@router.get("/api/job-postings/{jobId}")
def get_worker_job_posting(jobId: UUID, worker: CurrentWorker, db: SessionDep):
    at = common.now()
    row = db.execute(
        select(JobPosting, Store).join(Store, Store.id == JobPosting.store_id)
        .where(JobPosting.id == str(jobId), Store.approval_status == "APPROVED")
    ).first()
    if row is None:
        raise ApiError(404, ErrorCode.RESOURCE_NOT_FOUND)
    return worker_job_bodies(db, worker.user_id, [row], at)[0]
