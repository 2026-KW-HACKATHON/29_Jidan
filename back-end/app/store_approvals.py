"""Admin store approval requests (docs/store-approval-design.md).

There is no admin account: each request carries the shared admin password in its JSON body
(`security: []` in the spec). The router first requires an allowed `Origin` (403 CSRF_INVALID;
there is no session, so no CSRF token), then the `AdminAttempt` dependency reserves a rate-limit
slot, and the password is checked before any request data is read. The password is never
logged, echoed or stored. Each request that reaches an endpoint writes one operation
record (`operation_record`) with the action, approval request id and result; the log handler
adds the time and request ID (app.request_id). No password, applicant or store data.
"""

import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path, Request
from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator
from sqlalchemy import func, select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app import notification_events
from app.admin_password import authenticate_admin
from app.csrf import require_allowed_origin
from app.db import SessionDep, utcnow
from app.db.models import Store, StoreApprovalRequest, User
from app.errors import ApiError, ErrorCode
from app.idempotency import is_lock_contention
from app.pagination import MAX_SIZE, PageParams, page_response
from app.ratelimit import AdminAttempt
from app.store_access import APPROVED, PENDING, UUID_PATTERN, normalize_uuid
from app.stores import iso, store_fields

logger = logging.getLogger("jidan.admin")


def require_admin_origin(request: Request) -> None:
    """`require_allowed_origin`, recorded like a rate-limit refusal: the endpoint never runs.

    It runs before `AdminAttempt`, so a request from a foreign page neither checks the password
    nor spends an attempt. The record names the path only (never the Origin or the body).
    """
    try:
        require_allowed_origin(request)
    except ApiError as error:
        logger.warning("Admin operation refused: path=%s result=%s", request.url.path, error.code)
        raise


router = APIRouter(
    prefix="/api/admin/store-approval-requests", dependencies=[Depends(require_admin_origin)],
)

# Not stripped or shortened: the password is checked exactly as submitted.
AdminPassword = Annotated[str, Field(min_length=1, max_length=1024, pattern=r"\S", strict=True)]


class AdminBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    password: AdminPassword

    def __repr__(self) -> str:  # keep the password out of any accidental repr/log
        return f"{type(self).__name__}(password=***)"

    __str__ = __repr__


class ApprovalSearchBody(AdminBody):
    status: Literal["PENDING", "APPROVED"] | None = None
    page: Annotated[StrictInt, Field(ge=0)] = 0
    size: Annotated[StrictInt, Field(ge=1, le=MAX_SIZE)] = 20

    @field_validator("status", mode="before")
    @classmethod
    def non_null_status(cls, value):
        if value is None:
            raise ValueError("status must be omitted or PENDING/APPROVED")
        return value


@contextmanager
def operation_record(action: str, approval_request_id: str | None = None) -> Iterator[dict]:
    """Write one operation record per request that reached the endpoint.

    The record names the action, approval request id and result (an error code, or the outcome
    the endpoint stores in the yielded dict); the log handler adds the time and request ID.
    It never contains the password or applicant/store data.
    """
    outcome = {"result": "INTERNAL_ERROR"}
    try:
        yield outcome
    except ApiError as error:
        outcome["result"] = error.code
        raise
    finally:
        details = "".join(f" {key}={value}" for key, value in outcome.items() if key != "result")
        level = logging.INFO if outcome["result"] in SUCCESS_RESULTS else logging.WARNING
        logger.log(
            level, "Admin operation: action=%s approval_request_id=%s result=%s%s",
            action, approval_request_id or "-", outcome["result"], details,
        )


SUCCESS_RESULTS = {"OK", "APPROVED", "ALREADY_APPROVED"}


def approval_body(request: StoreApprovalRequest, store: Store, applicant: User) -> dict:
    return {
        "id": request.id,
        "applicant": {
            "id": applicant.id, "name": applicant.name, "email": applicant.google_email,
            "phoneNumber": applicant.phone_number,
        },
        "store": {"id": store.id, **store_fields(store)},
        "status": request.status,
        "submittedAt": iso(request.submitted_at),
        "approvedAt": iso(request.approved_at) if request.status == APPROVED else None,
    }


@router.post("/search")
def search_approval_requests(body: ApprovalSearchBody, attempt: AdminAttempt, db: SessionDep) -> dict:
    with operation_record("search") as record:
        authenticate_admin(body.password, attempt, action="search")
        base = (
            select(StoreApprovalRequest, Store, User)
            .join(Store, Store.id == StoreApprovalRequest.store_id)
            .join(User, User.id == Store.owner_id)
        )
        if body.status is not None:
            base = base.where(StoreApprovalRequest.status == body.status)
        params = PageParams(body.page, body.size)
        total = db.scalar(select(func.count()).select_from(base.subquery()))
        rows = [] if params.offset >= total else db.execute(
            base.order_by(StoreApprovalRequest.submitted_at.desc(), StoreApprovalRequest.id.desc())
            .offset(params.offset).limit(params.limit)
        ).all()
        record.update(result="OK", status=body.status or "ALL", page=body.page, count=len(rows))
        return page_response([approval_body(*row) for row in rows], total, params)


def active_owner(applicant: User | None) -> bool:
    return applicant is not None and applicant.role == "OWNER" and applicant.status == "ACTIVE"


def consistent(request: StoreApprovalRequest, store: Store) -> bool:
    """The request and its store agree on status and approval time.

    They change together, so a mismatch means one was changed outside this API. Approving or
    answering it as approved would hide that, so it is a 409 with nothing changed.
    """
    return request.status == store.approval_status and request.approved_at == store.approved_at


def not_allowed() -> ApiError:
    return ApiError(409, ErrorCode.STORE_APPROVAL_NOT_ALLOWED, "현재 신청자와 매장 관리 관계로 승인할 수 없습니다.")


# Attempts of the whole approval transaction on lock contention, and the base pause between them.
APPROVAL_ATTEMPTS = 3
APPROVAL_RETRY_DELAY_SECONDS = 0.05

RequestIdPath = Annotated[str, Path(alias="requestId", pattern=UUID_PATTERN)]


def approve_once(db: Session, request_id: str) -> tuple[StoreApprovalRequest, Store, User, bool]:
    """One approval transaction, committed: the rows and whether this call approved."""
    # Lock order: approval request, then store, then the applicant (shared). Concurrent
    # approvals of the same request wait on the request row; the conditional updates below
    # keep SQLite (no row locks) to one change as well. Every row is read with a lock so the
    # checks see the latest commit, not this transaction's snapshot (REPEATABLE READ).
    # 409 unless the applicant is an ACTIVE OWNER and the request and store agree.
    request = db.execute(
        select(StoreApprovalRequest).where(StoreApprovalRequest.id == request_id).with_for_update()
    ).scalar_one_or_none()
    if request is None:
        raise ApiError(404, ErrorCode.STORE_APPROVAL_REQUEST_NOT_FOUND, "매장 승인 신청을 찾을 수 없습니다.")
    store = db.execute(select(Store).where(Store.id == request.store_id).with_for_update()).scalar_one()
    # Shared: a status change waits for this approval, approvals of the owner's other stores do not.
    applicant = db.execute(
        select(User).where(User.id == store.owner_id).with_for_update(read=True)
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if not active_owner(applicant):
        raise not_allowed()

    now = utcnow()
    # Claim the PENDING request first. On MySQL the locks make this certain; SQLite has no row
    # locks, and another approval may have committed between the reads above (so the rows
    # read may disagree only because of it): the update then matches nothing.
    changed = request.status == PENDING and db.execute(
        update(StoreApprovalRequest)
        .where(StoreApprovalRequest.id == request_id, StoreApprovalRequest.status == PENDING)
        .values(status=APPROVED, approved_at=now)
        .execution_options(synchronize_session=False)
    ).rowcount == 1
    if changed:
        # The store moves with its request (PENDING, no time) to the same first approval time.
        if db.execute(
            update(Store)
            .where(Store.id == store.id, Store.approval_status == PENDING)
            .values(approval_status=APPROVED, approved_at=now)
            .execution_options(synchronize_session=False)
        ).rowcount != 1:
            raise not_allowed()  # an approved store with a PENDING request: rolled back
        notification_events.store_approved(db, store, at=now)
    else:
        db.refresh(request, with_for_update=True)
        db.refresh(store, with_for_update=True)
        if not consistent(request, store):
            raise not_allowed()
    db.commit()
    db.refresh(request)
    db.refresh(store)
    return request, store, applicant, changed


@router.post("/{requestId}/approve")
def approve_approval_request(
    request_id: RequestIdPath, body: AdminBody, attempt: AdminAttempt, db: SessionDep,
) -> dict:
    request_id = normalize_uuid(request_id)
    with operation_record("approve", request_id) as record:
        authenticate_admin(body.password, attempt, action="approve")
        for attempt_no in range(1, APPROVAL_ATTEMPTS + 1):
            try:
                request, store, applicant, changed = approve_once(db, request_id)
                break
            except OperationalError as error:
                # A deadlock victim (1213) or lock wait timeout (1205), e.g. against an outside
                # transaction taking the owner before the store: everything was rolled back, so
                # the whole approval runs again. After the last attempt it stays a 500.
                db.rollback()
                if not is_lock_contention(error) or attempt_no == APPROVAL_ATTEMPTS:
                    raise
                logger.warning("Admin approval retried after lock contention: attempt=%d", attempt_no)
                time.sleep(APPROVAL_RETRY_DELAY_SECONDS * attempt_no)
        record["result"] = "APPROVED" if changed else "ALREADY_APPROVED"
        return approval_body(request, store, applicant)
