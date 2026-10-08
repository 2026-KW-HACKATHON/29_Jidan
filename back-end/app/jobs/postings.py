"""Owner job postings: create, list by tab, detail and closure without a selection."""
from datetime import date, time
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select

from app.auth import CurrentOwner
from app.csrf import CsrfOwner
from app.db import SessionDep
from app.db.models import JobPosting
from app.errors import ApiError, ErrorCode
from app.idempotency import IdempotencyKey, IdempotentResult, run_idempotent
from app.jobs import common
from app.jobs.state import begin_transition, close_job, lock_job
from app.pagination import Pagination

router = APIRouter()

Clock = Annotated[str, Field(pattern=r"^([01][0-9]|2[0-3]):[0-5][0-9]$", strict=True)]
Notes = Annotated[str, Field(max_length=1000, strict=True)]


class JobPostingCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: Annotated[str, Field(min_length=1, max_length=100, strict=True)]
    description: Annotated[str, Field(min_length=1, max_length=3000, strict=True)]
    workPart: Literal["WEEKDAY_OPEN", "WEEKDAY_CLOSE", "WEEKEND_OPEN", "WEEKEND_CLOSE", "OTHER"]
    workDate: Annotated[str, Field(pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$", strict=True)]
    startTime: Clock
    endTime: Clock
    endsNextDay: Annotated[bool, Field(strict=True)]
    minimumExperience: Literal["ANY", "MONTHS_3", "MONTHS_6", "YEAR_1"]
    experienceNotes: Notes
    hourlyPay: Annotated[int, Field(ge=1, le=1_000_000, strict=True)]
    paymentTiming: Literal["WORK_DAY", "NEXT_DAY", "NEGOTIABLE"]
    paymentNotes: Notes

    @field_validator("workDate")
    @classmethod
    def real_date(cls, value: str) -> str:
        date.fromisoformat(value)  # rejects 2026-02-30 and friends
        return value


class JobClosure(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expectedRevision: Annotated[int, Field(ge=1, strict=True)]


def interval_error(message: str) -> ApiError:
    return ApiError(422, ErrorCode.INVALID_WORK_INTERVAL, message, field_errors=[
        {"field": "endTime", "code": "INVALID_FORMAT", "message": message},
    ])


def validated_times(body: JobPostingCreate, at) -> tuple[date, time, time]:
    """Seoul date/times of a future shift longer than 0 minutes and shorter than 24 hours.

    endsNextDay must say exactly whether the end clock falls on the next day: an end before
    the start without it is a negative shift, and with it an end after the start is longer
    than 24 hours. An end equal to the start is 0 minutes or exactly 24 hours, both invalid.
    """
    work_date = date.fromisoformat(body.workDate)
    start, end = time.fromisoformat(body.startTime), time.fromisoformat(body.endTime)
    if end == start or body.endsNextDay != (end < start):
        raise interval_error("근무 시간은 0분보다 길고 24시간보다 짧아야 합니다.")
    if work_date < common.seoul_today(at):
        raise interval_error("이미 시작된 근무는 등록할 수 없습니다.")
    try:
        times = common.shift_times(work_date, start, end, body.endsNextDay)
    except OverflowError:
        raise interval_error("근무 일시를 확인해 주세요.") from None
    if times.start_at <= at:
        raise interval_error("이미 시작된 근무는 등록할 수 없습니다.")
    return work_date, start, end


def posting_response(db, job: JobPosting, store, at) -> dict:
    return common.job_body(job, store, common.applicant_counts(db, [job.id], at).get(job.id, 0))


def status_tab(request: Request) -> str:
    values = request.query_params.getlist("status")
    if not values:
        return "RECRUITING"
    if len(values) > 1 or values[0] not in ("RECRUITING", "CLOSED"):
        raise ApiError(422, ErrorCode.VALIDATION_ERROR, field_errors=[
            {"field": "status", "code": "INVALID_FORMAT", "message": "RECRUITING 또는 CLOSED만 사용할 수 있습니다."},
        ])
    return values[0]


@router.post("/api/stores/{storeId}/job-postings", status_code=201)
def create_job_posting(storeId: UUID, body: JobPostingCreate, owner: CsrfOwner,
                       db: SessionDep, key: IdempotencyKey):
    store_id = str(storeId)

    def work() -> IdempotentResult:
        store = common.owned_store(db, owner.user_id, store_id)
        at = common.now()
        work_date, start, end = validated_times(body, at)
        job = JobPosting(
            store_id=store.id, created_by_owner_id=owner.user_id, title=body.title,
            duty_description=body.description, work_part=body.workPart, work_date=work_date,
            start_time=start, end_time=end, ends_next_day=body.endsNextDay, headcount=1,
            min_experience_months=common.EXPERIENCE_MONTHS[body.minimumExperience],
            extra_requirements=body.experienceNotes, hourly_wage_krw=body.hourlyPay,
            payment_timing=body.paymentTiming, pay_note=body.paymentNotes,
            status="RECRUITING", created_at=at, revision=1,
        )
        db.add(job)
        db.flush()
        return IdempotentResult(201, common.job_body(job, store, 0))

    return run_idempotent(
        db=db, principal=owner, key=key, method="POST",
        path=f"/api/stores/{store_id}/job-postings", body=body, handler=work,
        revalidate=lambda: common.owned_store(db, owner.user_id, store_id),
    )


@router.get("/api/stores/{storeId}/job-postings")
def list_job_postings(storeId: UUID, owner: CurrentOwner, db: SessionDep, page: Pagination,
                      status: Annotated[str, Depends(status_tab)]):
    store = common.owned_store(db, owner.user_id, str(storeId))
    as_of = common.now()
    condition = (JobPosting.store_id == store.id, JobPosting.status == status)
    total = db.scalar(select(func.count()).select_from(JobPosting).where(*condition))
    jobs = list(db.scalars(
        select(JobPosting).where(*condition)
        .order_by(JobPosting.created_at.desc(), JobPosting.id.desc())
        .offset(page.offset).limit(page.limit)
    ))
    counts = common.applicant_counts(db, (job.id for job in jobs), as_of)
    return {
        "items": [common.job_body(job, store, counts.get(job.id, 0)) for job in jobs],
        "page": page.page, "size": page.size, "totalItems": total, "asOf": common.iso(as_of),
    }


@router.get("/api/stores/{storeId}/job-postings/{jobId}")
def get_job_posting(storeId: UUID, jobId: UUID, owner: CurrentOwner, db: SessionDep):
    store = common.owned_store(db, owner.user_id, str(storeId))
    job = db.get(JobPosting, str(jobId))
    if job is None or job.store_id != store.id:
        raise ApiError(404, ErrorCode.RESOURCE_NOT_FOUND)
    return posting_response(db, job, store, common.now())


@router.post("/api/stores/{storeId}/job-postings/{jobId}/closure")
def close_job_posting(storeId: UUID, jobId: UUID, body: JobClosure, owner: CsrfOwner,
                      db: SessionDep, key: IdempotencyKey):
    store_id, job_id = str(storeId), str(jobId)

    def work() -> IdempotentResult:
        begin_transition(db)
        store = common.owned_store(db, owner.user_id, store_id)
        job = lock_job(db, store.id, job_id)
        if job.revision != body.expectedRevision:
            raise ApiError(409, ErrorCode.JOB_REVISION_CONFLICT, "공고가 변경되었습니다. 최신 상태를 확인해 주세요.")
        # An already CLOSED posting (accepted, or closed before) is answered as it is:
        # closedAt, revision, confirmation, access and schedule history stay untouched.
        at = common.now()
        if job.status == "RECRUITING":
            close_job(db, job, at)
        return IdempotentResult(200, posting_response(db, job, store, at))

    return run_idempotent(
        db=db, principal=owner, key=key, method="POST",
        path=f"/api/stores/{store_id}/job-postings/{job_id}/closure", body=body, handler=work,
        revalidate=lambda: common.owned_store(db, owner.user_id, store_id),
    )
