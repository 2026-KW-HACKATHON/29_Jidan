"""Password-authenticated store approval administration."""
import logging
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import Field, field_validator
from sqlalchemy import func, select

from app.admin_password import AdminPasswordInput, verify_password
from app.csrf import require_allowed_origin
from app.db import SessionDep, utcnow
from app.db.models import Store, StoreApprovalRequest, User
from app.errors import ApiError, ErrorCode, new_request_id
from app.owner_stores import read_snapshot, store_body
from app.pagination import PageParams, page_response
from app.ratelimit import AdminAttempt

router = APIRouter(prefix="/api/admin/store-approval-requests",
                   dependencies=[Depends(require_allowed_origin)])
logger = logging.getLogger("jidan.store_approval")


class ApprovalSearchInput(AdminPasswordInput):
    status: Literal["PENDING", "APPROVED"] | None = None
    page: Annotated[int, Field(ge=0, strict=True)] = 0
    size: Annotated[int, Field(ge=1, le=100, strict=True)] = 20

    @field_validator("status", mode="before")
    @classmethod
    def non_null_status(cls, value):
        if value is None:
            raise ValueError("Omit status instead of null")
        return value


def approval_body(approval, store, applicant):
    return {
        "id": approval.id, "applicant": {
            "id": applicant.id, "name": applicant.name,
            "email": applicant.google_email, "phoneNumber": applicant.phone_number,
        }, "store": store_body(store), "status": approval.status,
        "submittedAt": approval.submitted_at.isoformat(),
        "approvedAt": approval.approved_at.isoformat() if approval.approved_at else None,
    }


@router.post("/search")
def search_approvals(body: ApprovalSearchInput, attempt: AdminAttempt, db: SessionDep):
    verify_password(body.password, attempt)
    read_snapshot(db)
    filters = [] if body.status is None else [StoreApprovalRequest.status == body.status]
    total = db.scalar(select(func.count()).select_from(StoreApprovalRequest).where(*filters))
    params = PageParams(body.page, body.size)
    rows = []
    # Huge integer pages are valid contract input; avoid overflowing database OFFSET.
    if params.offset < total:
        rows = db.execute(select(StoreApprovalRequest, Store, User)
                          .join(Store, Store.id == StoreApprovalRequest.store_id)
                          .join(User, User.id == Store.owner_id).where(*filters)
                          .order_by(StoreApprovalRequest.submitted_at.desc(), StoreApprovalRequest.id.desc())
                          .offset(params.offset).limit(params.limit)).all()
    return page_response([approval_body(a, s, u) for a, s, u in rows], total, params)


@router.post("/{requestId}/approve")
def approve_store(requestId: UUID, body: AdminPasswordInput, attempt: AdminAttempt, db: SessionDep):
    verify_password(body.password, attempt)
    audit_id = new_request_id()
    def locked(model, row_id):
        return db.scalar(select(model).where(model.id == row_id).with_for_update()
                         .execution_options(populate_existing=True))
    try:
        approval = locked(StoreApprovalRequest, str(requestId))
        if approval is None:
            raise ApiError(404, ErrorCode.STORE_APPROVAL_REQUEST_NOT_FOUND)
        store = locked(Store, approval.store_id)
        applicant = locked(User, store.owner_id) if store is not None else None
        if applicant is None or applicant.role != "OWNER" or applicant.status != "ACTIVE":
            raise ApiError(409, ErrorCode.STORE_APPROVAL_NOT_ALLOWED)
        if (approval.status != store.approval_status or approval.approved_at != store.approved_at):
            raise ApiError(409, ErrorCode.STORE_APPROVAL_NOT_ALLOWED)
        changed = approval.status == "PENDING"
        if changed:
            now = utcnow()
            approval.status = store.approval_status = "APPROVED"
            approval.approved_at = store.approved_at = now
            db.flush()
        result = approval_body(approval, store, applicant)
        db.commit()  # status and the derived session permissions become durable together
    except ApiError as exc:
        logger.info("operation=approve request_id=%s approval_request_id=%s at=%s result=%s",
                    audit_id, requestId, utcnow().isoformat(), exc.code)
        raise
    logger.info("operation=approve request_id=%s approval_request_id=%s at=%s result=%s",
                audit_id, requestId, utcnow().isoformat(), "approved" if changed else "already_approved")
    return result
