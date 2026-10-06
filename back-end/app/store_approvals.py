"""Password-authenticated store approval administration."""
from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import Field, field_validator
from sqlalchemy import func, select

from app.admin_password import AdminPasswordInput, verify_password
from app.csrf import require_allowed_origin
from app.db import SessionDep
from app.db.models import Store, StoreApprovalRequest, User
from app.owner_stores import read_snapshot, store_body
from app.pagination import PageParams, page_response
from app.ratelimit import AdminAttempt

router = APIRouter(prefix="/api/admin/store-approval-requests",
                   dependencies=[Depends(require_allowed_origin)])


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

