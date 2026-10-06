"""Work requests (#114): owner sends / lists / withdraws, worker lists / answers, confirmation
withdrawal and the confirmed worker's onboarding link.

State machine (docs/work-request-design.md):

    request  PENDING -> ACCEPTED | DECLINED | EXPIRED (at expiresAt) | CANCELLED
             ACCEPTED -> CONFIRMATION_WITHDRAWN (owner, before the shift starts)
    application APPLIED -> REQUESTED -> APPLIED (decline / expiry / owner withdrawal)
                REQUESTED -> CONFIRMED -> APPLIED (confirmation withdrawal)
    posting  RECRUITING -> CLOSED (accept) -> RECRUITING (confirmation withdrawal)

A PENDING request at or past expiresAt is EXPIRED in every read and is recorded as such by
the next write on its posting; nothing extends the deadline.
"""
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import notification_events
from app.auth import CurrentOwner, CurrentWorker
from app.csrf import CsrfOwner, CsrfWorker
from app.db import SessionDep
from app.db.models import (
    JobApplication,
    JobPosting,
    ShiftAssignment,
    Store,
    StoreAccessGrant,
    WorkRequest,
)
from app.errors import ApiError, ErrorCode
from app.idempotency import IdempotencyKey, IdempotentResult, run_idempotent
from app.jobs import common
from app.jobs.access import grant_is_usable, published_manual_version
from app.jobs.state import (
    accept_request,
    begin_transition,
    cancel_request,
    decline_request,
    live_shift,
    lock_job,
    lock_worker,
    locked,
    overlapping_shift,
    send_request,
    settle_expired_requests,
    share_store,
    withdraw_confirmation,
)
from app.pagination import Pagination
from app.store_access import grant_body

router = APIRouter()

Revision = Annotated[int, Field(ge=1, strict=True)]


class WorkRequestCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expectedJobRevision: Revision


class WorkRequestResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expectedRevision: Revision
    decision: Literal["ACCEPT", "DECLINE"]


class WorkRequestWithdrawal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expectedRevision: Revision


# --- serialization -------------------------------------------------------------------------


def effective_request(request: WorkRequest, at: datetime) -> tuple[str, datetime | None]:
    """(status, endedAt) with time applied: a PENDING request is EXPIRED from expiresAt on."""
    if request.status == "PENDING" and request.expires_at <= at:
        return "EXPIRED", request.expires_at
    return request.status, request.ended_at


def request_bodies(db: Session, requests: list[WorkRequest], at: datetime) -> list[dict]:
    """`WorkRequest` schema; the access grant is the confirmation's TEMPORARY access."""
    if not requests:
        return []
    applications = {a.id: a for a in db.scalars(select(JobApplication).where(
        JobApplication.id.in_({r.application_id for r in requests})))}
    grants = {
        request_id: grant for request_id, grant in db.execute(
            select(ShiftAssignment.work_request_id, StoreAccessGrant)
            .join(StoreAccessGrant, StoreAccessGrant.assignment_id == ShiftAssignment.id)
            .where(ShiftAssignment.work_request_id.in_([r.id for r in requests]))
        ).all()
    }
    bodies = []
    for request in requests:
        application = applications[request.application_id]
        status, ended_at = effective_request(request, at)
        grant = grants.get(request.id) if status in ("ACCEPTED", "CONFIRMATION_WITHDRAWN") else None
        bodies.append({
            "id": request.id,
            "jobId": application.job_id,
            "applicationId": application.id,
            "workerId": application.worker_id,
            "workerName": application.applicant_name,
            "status": status,
            "requestedAt": common.iso(request.requested_at),
            "expiresAt": common.iso(request.expires_at),
            "respondedAt": common.iso(request.responded_at),
            "endedAt": common.iso(ended_at),
            "revision": request.revision,
            "accessGrant": grant_body(grant, at) if grant is not None else None,
        })
    return bodies


def request_body(db: Session, request: WorkRequest, at: datetime) -> dict:
    return request_bodies(db, [request], at)[0]


def page_body(db: Session, statement, page, at: datetime) -> dict:
    total = db.scalar(select(func.count()).select_from(statement.subquery()))
    requests = list(db.scalars(
        statement.order_by(WorkRequest.requested_at.desc(), WorkRequest.id.desc())
        .offset(page.offset).limit(page.limit)
    ))
    return {
        "items": request_bodies(db, requests, at),
        "page": page.page, "size": page.size, "totalItems": total, "asOf": common.iso(at),
    }


def request_filter(request: Request) -> str:
    values = request.query_params.getlist("filter")
    if not values:
        return "PENDING"
    if len(values) > 1 or values[0] not in ("PENDING", "ALL"):
        raise ApiError(422, ErrorCode.VALIDATION_ERROR, field_errors=[
            {"field": "filter", "code": "INVALID_FORMAT", "message": "PENDING 또는 ALL만 사용할 수 있습니다."},
        ])
    return values[0]


# --- lookups -------------------------------------------------------------------------------


def owner_job(db: Session, owner_id: str, store_id: str, job_id: str) -> tuple[Store, JobPosting]:
    store = common.owned_store(db, owner_id, store_id)
    job = db.get(JobPosting, job_id)
    if job is None or job.store_id != store.id:
        raise ApiError(404, ErrorCode.RESOURCE_NOT_FOUND)
    return store, job


def job_request(db: Session, job_id: str, request_id: str) -> WorkRequest:
    """A request of this posting; any other request is the same 404 as a missing one."""
    request = db.scalar(
        select(WorkRequest).join(JobApplication, JobApplication.id == WorkRequest.application_id)
        .where(WorkRequest.id == request_id, JobApplication.job_id == job_id)
    )
    if request is None:
        raise ApiError(404, ErrorCode.RESOURCE_NOT_FOUND)
    return request


def own_request(db: Session, worker_id: str, request_id: str) -> tuple[WorkRequest, JobApplication]:
    row = db.execute(
        select(WorkRequest, JobApplication).join(JobApplication, JobApplication.id == WorkRequest.application_id)
        .where(WorkRequest.id == request_id, JobApplication.worker_id == worker_id)
    ).first()
    if row is None:
        raise ApiError(404, ErrorCode.RESOURCE_NOT_FOUND)
    return row[0], row[1]


def lock_request(db: Session, job: JobPosting, request_id: str,
                 at: datetime) -> tuple[WorkRequest, JobApplication]:
    """Lock request -> application under the posting lock, recording expiry of requests due
    at `at` first. Use the same `at` for the transition so a request cannot expire between
    this check and the change."""
    settle_expired_requests(db, job.id, at)
    request = db.scalar(locked(select(WorkRequest).where(WorkRequest.id == request_id)))
    application = db.scalar(locked(select(JobApplication).where(JobApplication.id == request.application_id)))
    return request, application


def require_pending(request: WorkRequest) -> None:
    if request.status == "EXPIRED":
        raise ApiError(409, ErrorCode.WORK_REQUEST_EXPIRED, "응답 기한이 지난 요청입니다.")
    if request.status != "PENDING":
        raise ApiError(409, ErrorCode.WORK_REQUEST_NOT_PENDING, "이미 종료된 요청입니다.")


def require_revision(request: WorkRequest, expected: int) -> None:
    if request.revision != expected:
        raise ApiError(409, ErrorCode.WORK_REQUEST_REVISION_CONFLICT, "요청 상태가 변경되었습니다. 최신 상태를 확인해 주세요.")


# --- owner ---------------------------------------------------------------------------------


@router.post("/api/stores/{storeId}/job-postings/{jobId}/applications/{applicationId}/work-requests",
             status_code=201)
def send_work_request(storeId: UUID, jobId: UUID, applicationId: UUID, body: WorkRequestCreate,
                      owner: CsrfOwner, db: SessionDep, key: IdempotencyKey):
    store_id, job_id, application_id = str(storeId), str(jobId), str(applicationId)

    def work() -> IdempotentResult:
        begin_transition(db)
        store = common.owned_store(db, owner.user_id, store_id)
        job = lock_job(db, store.id, job_id)
        at = common.now()
        live = settle_expired_requests(db, job.id, at)
        application = db.scalar(locked(select(JobApplication).where(JobApplication.id == application_id)))
        if application is None or application.job_id != job.id:
            raise ApiError(404, ErrorCode.RESOURCE_NOT_FOUND)
        if job.revision != body.expectedJobRevision:
            raise ApiError(409, ErrorCode.JOB_REVISION_CONFLICT, "공고가 변경되었습니다. 최신 상태를 확인해 주세요.")
        if live_shift(db, job.id) is not None:
            raise ApiError(409, ErrorCode.JOB_FILLED, "근무자가 확정된 공고입니다.")
        if job.status != "RECRUITING" or common.job_times(job).start_at <= at:
            raise ApiError(409, ErrorCode.JOB_NOT_RECRUITING, "모집 중이며 시작 전인 공고에만 요청할 수 있습니다.")
        if live:
            raise ApiError(409, ErrorCode.WORK_REQUEST_PENDING, "수락 대기 중인 요청이 있습니다.")
        if application.status != "APPLIED":
            raise ApiError(409, ErrorCode.APPLICATION_NOT_ACTIVE, "요청할 수 없는 지원입니다.")
        request = send_request(db, job, application, owner.user_id, at)
        # notification: WORK_REQUEST_RECEIVED (to application.worker_id, target = this request)
        notification_events.work_request_received(db, request, application, job)
        return IdempotentResult(201, request_body(db, request, at))

    return run_idempotent(
        db=db, principal=owner, key=key, method="POST",
        path=f"/api/stores/{store_id}/job-postings/{job_id}/applications/{application_id}/work-requests",
        body=body, handler=work, revalidate=lambda: common.owned_store(db, owner.user_id, store_id),
    )


@router.get("/api/stores/{storeId}/job-postings/{jobId}/work-requests")
def list_job_work_requests(storeId: UUID, jobId: UUID, owner: CurrentOwner, db: SessionDep,
                           page: Pagination):
    _store, job = owner_job(db, owner.user_id, str(storeId), str(jobId))
    statement = (select(WorkRequest).join(JobApplication, JobApplication.id == WorkRequest.application_id)
                 .where(JobApplication.job_id == job.id))
    return page_body(db, statement, page, common.now())


@router.post("/api/stores/{storeId}/job-postings/{jobId}/work-requests/{requestId}/withdrawal")
def withdraw_work_request(storeId: UUID, jobId: UUID, requestId: UUID, body: WorkRequestWithdrawal,
                          owner: CsrfOwner, db: SessionDep, key: IdempotencyKey):
    store_id, job_id, request_id = str(storeId), str(jobId), str(requestId)

    def work() -> IdempotentResult:
        begin_transition(db)
        store = common.owned_store(db, owner.user_id, store_id)
        job = lock_job(db, store.id, job_id)
        job_request(db, job.id, request_id)
        at = common.now()
        request, application = lock_request(db, job, request_id, at)
        require_pending(request)
        require_revision(request, body.expectedRevision)
        cancel_request(db, job, request, application, at)
        # notification: WORK_REQUEST_WITHDRAWN (to application.worker_id, target = this request)
        notification_events.work_request_withdrawn(db, request, application, job)
        return IdempotentResult(200, request_body(db, request, at))

    return run_idempotent(
        db=db, principal=owner, key=key, method="POST",
        path=f"/api/stores/{store_id}/job-postings/{job_id}/work-requests/{request_id}/withdrawal",
        body=body, handler=work, revalidate=lambda: common.owned_store(db, owner.user_id, store_id),
    )


@router.post("/api/stores/{storeId}/job-postings/{jobId}/work-requests/{requestId}/confirmation-withdrawal")
def withdraw_work_confirmation(storeId: UUID, jobId: UUID, requestId: UUID, body: WorkRequestWithdrawal,
                               owner: CsrfOwner, db: SessionDep, key: IdempotencyKey):
    store_id, job_id, request_id = str(storeId), str(jobId), str(requestId)

    def work() -> IdempotentResult:
        begin_transition(db)
        store = common.owned_store(db, owner.user_id, store_id)
        job = lock_job(db, store.id, job_id)
        job_request(db, job.id, request_id)
        request = db.scalar(locked(select(WorkRequest).where(WorkRequest.id == request_id)))
        if request.status != "ACCEPTED":
            raise ApiError(409, ErrorCode.WORK_CONFIRMATION_NOT_ACTIVE, "철회할 수 있는 확정이 아닙니다.")
        at = common.now()
        if common.job_times(job).start_at <= at:
            raise ApiError(409, ErrorCode.JOB_ALREADY_STARTED, "이미 시작된 근무는 확정을 철회할 수 없습니다.")
        require_revision(request, body.expectedRevision)
        withdraw_confirmation(db, job, request, at)
        # notification: WORK_CONFIRMATION_WITHDRAWN (to the worker, target = this request)
        notification_events.work_confirmation_withdrawn(
            db, request, db.get(JobApplication, request.application_id), job,
        )
        return IdempotentResult(200, request_body(db, request, at))

    return run_idempotent(
        db=db, principal=owner, key=key, method="POST",
        path=f"/api/stores/{store_id}/job-postings/{job_id}/work-requests/{request_id}/confirmation-withdrawal",
        body=body, handler=work, revalidate=lambda: common.owned_store(db, owner.user_id, store_id),
    )


@router.get("/api/stores/{storeId}/job-postings/{jobId}/onboarding")
def get_onboarding(storeId: UUID, jobId: UUID, owner: CurrentOwner, db: SessionDep):
    store, job = owner_job(db, owner.user_id, str(storeId), str(jobId))
    shift = live_shift(db, job.id)
    if shift is None:
        raise ApiError(404, ErrorCode.ONBOARDING_NOT_FOUND, "확정된 근무자가 없습니다.")
    request = db.get(WorkRequest, shift.work_request_id)
    application = db.get(JobApplication, request.application_id)
    grant = db.scalar(select(StoreAccessGrant).where(StoreAccessGrant.assignment_id == shift.id))
    version = published_manual_version(db, store.id)
    return {
        "jobId": job.id,
        "workerId": shift.worker_id,
        "workerName": application.applicant_name,
        "accessStatus": "ACTIVE" if grant is not None and grant_is_usable(grant, common.now()) else "ENDED",
        "manualStatus": "PUBLISHED" if version is not None else "NOT_PUBLISHED",
        "manualVersionId": version,
    }


# --- worker --------------------------------------------------------------------------------


@router.get("/api/users/me/work-requests")
def list_my_work_requests(worker: CurrentWorker, db: SessionDep, page: Pagination,
                          view: Annotated[str, Depends(request_filter)]):
    at = common.now()
    statement = (select(WorkRequest).join(JobApplication, JobApplication.id == WorkRequest.application_id)
                 .where(JobApplication.worker_id == worker.user_id))
    if view == "PENDING":
        statement = statement.where(WorkRequest.status == "PENDING", WorkRequest.expires_at > at)
    return page_body(db, statement, page, at)


@router.get("/api/users/me/work-requests/{requestId}")
def get_my_work_request(requestId: UUID, worker: CurrentWorker, db: SessionDep):
    request, _application = own_request(db, worker.user_id, str(requestId))
    return request_body(db, request, common.now())


@router.post("/api/users/me/work-requests/{requestId}/response")
def respond_to_work_request(requestId: UUID, body: WorkRequestResponse, worker: CsrfWorker,
                            db: SessionDep, key: IdempotencyKey):
    request_id = str(requestId)

    def work() -> IdempotentResult:
        begin_transition(db)
        _request, found = own_request(db, worker.user_id, request_id)
        job = db.get(JobPosting, found.job_id)
        share_store(db, job.store_id)  # store -> posting, as the store domain locks
        job = lock_job(db, job.store_id, job.id)
        at = common.now()
        request, application = lock_request(db, job, request_id, at)
        require_pending(request)
        require_revision(request, body.expectedRevision)
        if body.decision == "DECLINE":
            decline_request(db, job, request, application, at)
            return IdempotentResult(200, request_body(db, request, at))
        if application.status != "REQUESTED":
            raise ApiError(409, ErrorCode.APPLICATION_WITHDRAWN, "철회된 지원입니다.")
        if live_shift(db, job.id, lock=True) is not None:
            raise ApiError(409, ErrorCode.JOB_FILLED, "근무자가 확정된 공고입니다.")
        if job.status != "RECRUITING":
            raise ApiError(409, ErrorCode.JOB_CLOSED, "모집이 마감된 공고입니다.")
        lock_worker(db, worker.user_id)
        if overlapping_shift(db, worker.user_id, job) is not None:
            raise ApiError(409, ErrorCode.WORK_INTERVAL_CONFLICT, "이미 확정된 다른 근무와 시간이 겹칩니다.")
        accept_request(db, job, request, application, at)
        # notification: WORK_CONFIRMED (to the store owner and the worker, target = this request)
        notification_events.work_confirmed(db, request, application, job)
        return IdempotentResult(200, request_body(db, request, at))

    return run_idempotent(
        db=db, principal=worker, key=key, method="POST",
        path=f"/api/users/me/work-requests/{request_id}/response", body=body, handler=work,
    )
