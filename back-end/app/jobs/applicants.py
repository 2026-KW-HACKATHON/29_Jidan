"""Owner applicant review (#113): applicants of an owned posting and one application.

Only what the applicant submitted is shown: introduction and the name/age/careers snapshot
taken at submission. Contact details, birthday, Google email and other applications never
leave this module.
"""
from collections import defaultdict
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import CurrentOwner
from app.db import SessionDep
from app.db.models import ApplicationCareer, JobApplication, JobPosting
from app.errors import ApiError, ErrorCode
from app.jobs import common
from app.jobs.applications import due_requested, effective_status
from app.pagination import Pagination

router = APIRouter()


def owned_job(db: Session, owner_id: str, store_id: str, job_id: str) -> JobPosting:
    store = common.owned_store(db, owner_id, store_id)
    job = db.get(JobPosting, job_id)
    if job is None or job.store_id != store.id:
        raise ApiError(404, ErrorCode.RESOURCE_NOT_FOUND)
    return job


def career_body(career: ApplicationCareer) -> dict:
    body = {
        "industry": career.industry, "duties": career.duties, "startMonth": career.start_month,
        "endMonth": career.end_month, "isCurrent": career.is_current,
    }
    if career.store_name is not None:  # optional in the contract: omitted, never null
        body["storeName"] = career.store_name
    return body


def applicant_bodies(db: Session, job: JobPosting, applications: list[JobApplication],
                     at: datetime) -> list[dict]:
    """`OwnerJobApplication` schema for applications of one posting."""
    careers = defaultdict(list)
    if applications:
        for career in db.scalars(
            select(ApplicationCareer)
            .where(ApplicationCareer.application_id.in_([a.id for a in applications]))
            .order_by(ApplicationCareer.application_id, ApplicationCareer.sort_order)
        ):
            careers[career.application_id].append(career_body(career))
    due = due_requested(db, applications, at)
    return [{
        "id": application.id,
        "jobId": job.id,
        "status": effective_status(application, job, application.id in due, at),
        "introduction": application.introduction,
        "applicant": {
            "workerId": application.worker_id,
            "name": application.applicant_name,
            "ageAtSubmission": application.age_at_submission,
            "experienceLevel": application.experience_level,
            "careers": careers[application.id],
        },
        "submittedAt": common.iso(application.applied_at),
        "revision": application.revision,
    } for application in applications]


def active_condition(job: JobPosting, at: datetime):
    """APPLIED/REQUESTED before the shift starts (NOT_SELECTED after), and CONFIRMED until it
    ends (COMPLETED after)."""
    times = common.job_times(job)
    statuses = []
    if times.start_at > at:
        statuses += common.OPEN_APPLICATION_STATUSES
    if times.end_at > at:
        statuses.append("CONFIRMED")
    return JobApplication.status.in_(statuses)


def applicant_filter(request: Request) -> str:
    values = request.query_params.getlist("filter")
    if not values:
        return "ACTIVE"
    if len(values) > 1 or values[0] not in ("ACTIVE", "ALL"):
        raise ApiError(422, ErrorCode.VALIDATION_ERROR, field_errors=[
            {"field": "filter", "code": "INVALID_FORMAT", "message": "ACTIVE 또는 ALL만 사용할 수 있습니다."},
        ])
    return values[0]


@router.get("/api/stores/{storeId}/job-postings/{jobId}/applications")
def list_applicants(storeId: UUID, jobId: UUID, owner: CurrentOwner, db: SessionDep,
                    page: Pagination, view: Annotated[str, Depends(applicant_filter)]):
    job = owned_job(db, owner.user_id, str(storeId), str(jobId))
    at = common.now()
    condition = [JobApplication.job_id == job.id]
    if view == "ACTIVE":
        condition.append(active_condition(job, at))
    total = db.scalar(select(func.count()).select_from(JobApplication).where(*condition))
    applications = list(db.scalars(
        select(JobApplication).where(*condition)
        .order_by(JobApplication.applied_at, JobApplication.id)
        .offset(page.offset).limit(page.limit)
    ))
    return {
        "items": applicant_bodies(db, job, applications, at),
        "page": page.page, "size": page.size, "totalItems": total, "asOf": common.iso(at),
    }


@router.get("/api/stores/{storeId}/job-postings/{jobId}/applications/{applicationId}")
def get_applicant(storeId: UUID, jobId: UUID, applicationId: UUID, owner: CurrentOwner,
                  db: SessionDep):
    job = owned_job(db, owner.user_id, str(storeId), str(jobId))
    application = db.get(JobApplication, str(applicationId))
    if application is None or application.job_id != job.id:
        raise ApiError(404, ErrorCode.RESOURCE_NOT_FOUND)
    return applicant_bodies(db, job, [application], common.now())[0]
