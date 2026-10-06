"""Worker applications (#112): apply with a profile snapshot, list, detail and withdraw.

Every attempt is its own row. After a withdrawal the worker re-applies with a new
Idempotency-Key and gets a new application; the old WITHDRAWN row and its cancelled requests
stay as they were. The partial UNIQUE (job_id, active_worker_id) keeps at most one
APPLIED/REQUESTED/CONFIRMED application per posting and worker even if a lock is missed.
"""
from datetime import date, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import notification_events
from app.auth import CurrentWorker
from app.csrf import CsrfWorker
from app.db import SessionDep
from app.db.models import (
    ApplicationCareer,
    JobApplication,
    JobPosting,
    Store,
    User,
    WorkerCareer,
    WorkerProfile,
    WorkRequest,
)
from app.errors import ApiError, ErrorCode
from app.idempotency import IdempotencyKey, IdempotentResult, run_idempotent
from app.jobs import common
from app.jobs.search import filled_jobs
from app.jobs.state import (
    LIVE_APPLICATION_STATUSES,
    begin_transition,
    bump,
    lock_each,
    lock_job,
    locked,
    settle_expired_requests,
)
from app.pagination import Pagination

router = APIRouter()

TABS = ("PENDING", "CONFIRMED", "ENDED")


class ApplicationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    introduction: Annotated[str, Field(min_length=1, max_length=500, strict=True)]


class ApplicationRevision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expectedRevision: Annotated[int, Field(ge=1, strict=True)]


def age_on(birth: date, today: date) -> int:
    """Full years (the Korean legal "만 나이")."""
    return today.year - birth.year - ((today.month, today.day) < (birth.month, birth.day))


# --- projection ----------------------------------------------------------------------------


def due_requested(db: Session, applications: list[JobApplication], at: datetime) -> set[str]:
    """REQUESTED applications whose pending request reached its deadline (read as APPLIED)."""
    ids = [a.id for a in applications if a.status == "REQUESTED"]
    if not ids:
        return set()
    live = set(db.scalars(select(WorkRequest.application_id).where(
        WorkRequest.application_id.in_(ids), WorkRequest.status == "PENDING",
        WorkRequest.expires_at > at,
    )))
    return set(ids) - live


def effective_status(application: JobApplication, job: JobPosting, due: bool, at: datetime) -> str:
    """Stored status with time applied: an application still open when the shift starts reads
    NOT_SELECTED, an expired request frees the application, and a confirmed shift that has
    ended reads as COMPLETED."""
    if common.unselected_at_start(application.status, job, at):
        return "NOT_SELECTED"
    if application.status == "REQUESTED" and due:
        return "APPLIED"
    if application.status == "CONFIRMED" and common.job_times(job).end_at <= at:
        return "COMPLETED"
    return application.status


def application_bodies(db: Session, rows: list[tuple[JobApplication, JobPosting, Store]],
                       at: datetime) -> list[dict]:
    """`JobApplication` schema for each (application, posting, store)."""
    counts = common.applicant_counts(db, {job.id for _a, job, _s in rows}, at)
    due = due_requested(db, [a for a, _j, _s in rows], at)
    return [{
        "id": application.id,
        "job": common.job_body(job, store, counts.get(job.id, 0)),
        "status": effective_status(application, job, application.id in due, at),
        "introduction": application.introduction,
        "submittedAt": common.iso(application.applied_at),
        "withdrawnAt": common.iso(application.withdrawn_at),
        "revision": application.revision,
    } for application, job, store in rows]


def application_body(db: Session, application: JobApplication, at: datetime) -> dict:
    job = db.get(JobPosting, application.job_id)
    return application_bodies(db, [(application, job, db.get(Store, job.store_id))], at)[0]


def tab_condition(tab: str, at: datetime):
    """SQL for the list tabs; must agree with `effective_status`.

    The statement must join JobPosting. REQUESTED with a due request is still PENDING (it
    reads as APPLIED); an APPLIED/REQUESTED application whose shift has started moves to ENDED
    as NOT_SELECTED, and a CONFIRMED one whose shift ended moves there as COMPLETED.
    """
    is_open = JobApplication.status.in_(common.OPEN_APPLICATION_STATUSES)
    if tab == "PENDING":
        return and_(is_open, common.starts_after(at))
    if tab == "CONFIRMED":
        return and_(JobApplication.status == "CONFIRMED", common.ends_after(at))
    return or_(JobApplication.status.in_(("WITHDRAWN", "NOT_SELECTED", "COMPLETED")),
               and_(is_open, ~common.starts_after(at)),
               and_(JobApplication.status == "CONFIRMED", ~common.ends_after(at)))


def application_tab(request: Request) -> str:
    values = request.query_params.getlist("tab")
    if not values:
        return "PENDING"
    if len(values) > 1 or values[0] not in TABS:
        raise ApiError(422, ErrorCode.VALIDATION_ERROR, field_errors=[
            {"field": "tab", "code": "INVALID_FORMAT", "message": "PENDING, CONFIRMED, ENDED 중 하나입니다."},
        ])
    return values[0]


def own_application(db: Session, worker_id: str, application_id: str) -> JobApplication:
    """The caller's application; anyone else's is the same 404 as a missing one."""
    application = db.get(JobApplication, application_id)
    if application is None or application.worker_id != worker_id:
        raise ApiError(404, ErrorCode.RESOURCE_NOT_FOUND)
    return application


# --- apply ---------------------------------------------------------------------------------


def apply_conflict(db: Session, job: JobPosting, worker_id: str, at: datetime) -> ApiError | None:
    """Why a new application is refused, checked under the posting lock.

    The caller's own live application wins, then a filled posting (before the general
    CLOSED), then closed, then started.
    """
    live = next((application for application in lock_each(db, JobApplication, select(JobApplication.id).where(
        JobApplication.job_id == job.id, JobApplication.worker_id == worker_id,
        JobApplication.status.in_(LIVE_APPLICATION_STATUSES),
    )) if application.status in LIVE_APPLICATION_STATUSES), None)
    if live is not None and not common.unselected_at_start(live.status, job, at):
        return ApiError(409, ErrorCode.APPLICATION_ALREADY_ACTIVE, "이미 지원한 공고입니다.")
    if filled_jobs(db, [job.id]):
        return ApiError(409, ErrorCode.JOB_FILLED, "근무자가 확정된 공고입니다.")
    if job.status != "RECRUITING":
        return ApiError(409, ErrorCode.JOB_NOT_RECRUITING, "모집이 마감된 공고입니다.")
    if common.job_times(job).start_at <= at:
        return ApiError(409, ErrorCode.JOB_STARTED, "이미 시작된 근무입니다.")
    return None


def snapshot_application(db: Session, job: JobPosting, worker_id: str, introduction: str,
                         at: datetime) -> JobApplication:
    """New application with the worker's name, age and careers as of now.

    A profile save locks the worker row and changes level and careers together; the share
    lock waits for it, so name, level and careers come from one committed state (posting ->
    worker, the same order as an acceptance).
    """
    user = db.scalar(select(User).where(User.id == worker_id).with_for_update(read=True)
                     .execution_options(populate_existing=True))
    profile = db.get(WorkerProfile, worker_id)
    application = JobApplication(
        job_id=job.id, worker_id=worker_id, introduction=introduction, status="APPLIED",
        applied_at=at, applicant_name=user.name,
        age_at_submission=age_on(profile.birth_date, common.seoul_today(at)),
        experience_level=profile.experience_level, revision=1,
    )
    db.add(application)
    db.flush()
    careers = db.scalars(select(WorkerCareer).where(WorkerCareer.worker_id == worker_id)
                         .order_by(WorkerCareer.sort_order))
    db.add_all(ApplicationCareer(
        application_id=application.id, sort_order=career.sort_order, industry=career.industry,
        duties=career.duties, store_name=career.store_name, start_month=career.start_month,
        end_month=career.end_month, is_current=career.is_current,
    ) for career in careers)
    db.flush()
    return application


@router.post("/api/job-postings/{jobId}/applications", status_code=201)
def apply_for_job(jobId: UUID, body: ApplicationCreate, worker: CsrfWorker, db: SessionDep,
                  key: IdempotencyKey):
    job_id = str(jobId)

    def work() -> IdempotentResult:
        begin_transition(db)
        job = db.scalar(locked(select(JobPosting).where(JobPosting.id == job_id)))
        # A posting of a store that is not APPROVED is hidden like a missing one.
        if job is None or db.get(Store, job.store_id).approval_status != "APPROVED":
            raise ApiError(404, ErrorCode.RESOURCE_NOT_FOUND)
        at = common.now()
        conflict = apply_conflict(db, job, worker.user_id, at)
        if conflict is not None:
            raise conflict
        application = snapshot_application(db, job, worker.user_id, body.introduction, at)
        bump(job)
        db.flush()
        # notification: NEW_APPLICATION (to the store owner, target = this application)
        notification_events.new_application(db, application, job)
        return IdempotentResult(201, application_body(db, application, at))

    try:
        return run_idempotent(
            db=db, principal=worker, key=key, method="POST",
            path=f"/api/job-postings/{job_id}/applications", body=body, handler=work,
        )
    except IntegrityError:
        # run_idempotent already rolled back and released the key. Only the partial UNIQUE
        # catching a concurrent live application maps to the public code.
        if db.scalar(select(JobApplication.id).where(
            JobApplication.job_id == job_id, JobApplication.worker_id == worker.user_id,
            JobApplication.status.in_(LIVE_APPLICATION_STATUSES),
        )) is not None:
            raise ApiError(409, ErrorCode.APPLICATION_ALREADY_ACTIVE, "이미 지원한 공고입니다.") from None
        raise


# --- read ----------------------------------------------------------------------------------


@router.get("/api/users/me/applications")
def list_my_applications(worker: CurrentWorker, db: SessionDep, page: Pagination,
                         tab: Annotated[str, Depends(application_tab)]):
    at = common.now()
    base = (
        select(JobApplication, JobPosting, Store)
        .join(JobPosting, JobPosting.id == JobApplication.job_id)
        .join(Store, Store.id == JobPosting.store_id)
        .where(JobApplication.worker_id == worker.user_id)
    )
    tally = db.execute(
        select(*(func.coalesce(func.sum(case((tab_condition(name, at), 1), else_=0)), 0) for name in TABS))
        .select_from(JobApplication).join(JobPosting, JobPosting.id == JobApplication.job_id)
        .where(JobApplication.worker_id == worker.user_id)
    ).one()
    counts = dict(zip(TABS, (int(value) for value in tally), strict=True))
    rows = list(db.execute(
        base.where(tab_condition(tab, at))
        .order_by(JobApplication.applied_at.desc(), JobApplication.id.desc())
        .offset(page.offset).limit(page.limit)
    ).all())
    return {
        "items": application_bodies(db, rows, at),
        "page": page.page, "size": page.size, "totalItems": counts[tab], "asOf": common.iso(at),
        "counts": {"pending": counts["PENDING"], "confirmed": counts["CONFIRMED"], "ended": counts["ENDED"]},
    }


@router.get("/api/users/me/applications/{applicationId}")
def get_my_application(applicationId: UUID, worker: CurrentWorker, db: SessionDep):
    application = own_application(db, worker.user_id, str(applicationId))
    return application_body(db, application, common.now())


# --- withdraw ------------------------------------------------------------------------------


def withdraw(db: Session, application: JobApplication, at: datetime) -> None:
    """APPLIED/REQUESTED -> WITHDRAWN; a live pending request of it is CANCELLED with it."""
    for request in lock_each(db, WorkRequest, select(WorkRequest.id).where(
        WorkRequest.application_id == application.id, WorkRequest.status == "PENDING",
    )):
        if request.status != "PENDING":
            continue
        request.status = "CANCELLED"
        request.ended_at = at
        bump(request)
    application.status = "WITHDRAWN"
    application.withdrawn_at = at
    bump(application)


@router.post("/api/users/me/applications/{applicationId}/withdrawal")
def withdraw_application(applicationId: UUID, body: ApplicationRevision, worker: CsrfWorker,
                         db: SessionDep, key: IdempotencyKey):
    application_id = str(applicationId)

    def work() -> IdempotentResult:
        begin_transition(db)
        found = own_application(db, worker.user_id, application_id)
        job = db.get(JobPosting, found.job_id)
        job = lock_job(db, job.store_id, job.id)  # posting -> requests -> application
        at = common.now()
        settle_expired_requests(db, job.id, at)
        application = db.scalar(locked(select(JobApplication).where(JobApplication.id == application_id)))
        if application.revision != body.expectedRevision:
            raise ApiError(409, ErrorCode.APPLICATION_REVISION_CONFLICT, "지원 상태가 변경되었습니다. 최신 상태를 확인해 주세요.")
        if common.unselected_at_start(application.status, job, at):  # reads NOT_SELECTED
            raise ApiError(409, ErrorCode.APPLICATION_NOT_WITHDRAWABLE, "철회할 수 없는 지원입니다.")
        if application.status in ("APPLIED", "REQUESTED"):
            withdraw(db, application, at)
            bump(job)
            db.flush()
        elif application.status != "WITHDRAWN":
            raise ApiError(409, ErrorCode.APPLICATION_NOT_WITHDRAWABLE, "철회할 수 없는 지원입니다.")
        return IdempotentResult(200, application_body(db, application, at))

    return run_idempotent(
        db=db, principal=worker, key=key, method="POST",
        path=f"/api/users/me/applications/{application_id}/withdrawal", body=body, handler=work,
    )
