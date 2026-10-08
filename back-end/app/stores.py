"""Owner store management (docs/owner-store-design.md).

Every request re-checks the session owner's ownership and, for operating features, the store's
approval (app.store_access). Permissions in responses are UI hints derived from the current
approval status; nothing is cached.
"""

from datetime import datetime

from fastapi import APIRouter
from sqlalchemy import case, func, select
from sqlalchemy.exc import IntegrityError, OperationalError

from app.auth import CurrentOwner
from app.auth_views import STORE_PERMISSIONS
from app.csrf import CsrfOwner
from app.db import SessionDep, iso_utc, utcnow
from app.db.models import Store, StoreAccessGrant, StoreApprovalRequest, StoreInvitation, User
from app.errors import ApiError, ErrorCode
from app.idempotency import IdempotencyKey, IdempotentResult, is_lock_contention, run_idempotent
from app.pagination import Pagination, page_response
from app.registration_inputs import StoreInput
from app.store_access import (
    APPROVED,
    PENDING,
    StoreIdPath,
    lasting_grant_clause,
    load_owned_store,
    pending_invitation_clause,
    valid_grant_clause,
)
from app.store_address import verify_store_address

router = APIRouter()

PENDING_PERMISSIONS = ["READ_STORE_STATUS"]


def iso(value: datetime | None) -> str | None:
    return iso_utc(value)


def store_permissions(store: Store) -> list[str]:
    return list(STORE_PERMISSIONS) if store.approval_status == APPROVED else list(PENDING_PERMISSIONS)


def store_fields(store: Store) -> dict:
    """The StoreRegistrationInput fields as stored; detailAddress is omitted when absent."""
    body = {
        "name": store.name, "industry": store.industry, "postalCode": store.postal_code,
        "address": store.address,
        "businessRegistrationNumber": store.business_registration_number,
        "phoneNumber": store.phone_number,
    }
    if store.detail_address is not None:
        body["detailAddress"] = store.detail_address
    return body


def owner_store_body(store: Store, approval_request_id: str) -> dict:
    return {
        **store_fields(store),
        "id": store.id,
        "approvalRequestId": approval_request_id,
        "approvalStatus": store.approval_status,
        "createdAt": iso(store.created_at),
        "approvedAt": iso(store.approved_at) if store.approval_status == APPROVED else None,
        "permissions": store_permissions(store),
    }


def _approval_request_id(db, store_id: str) -> str:
    return db.scalar(select(StoreApprovalRequest.id).where(StoreApprovalRequest.store_id == store_id))


@router.get("/api/owners/me/stores")
def list_my_stores(owner: CurrentOwner, db: SessionDep, params: Pagination) -> dict:
    as_of = utcnow()
    # Every store is created with its approval request in one transaction, so the inner join
    # drops nothing; it keeps approvalRequestId (required) from ever being null.
    base = (
        select(Store, StoreApprovalRequest.id)
        .join(StoreApprovalRequest, StoreApprovalRequest.store_id == Store.id)
        .where(Store.owner_id == owner.user_id)
    )
    total = db.scalar(select(func.count()).select_from(base.subquery()))
    rows = db.execute(
        base.order_by(Store.created_at.desc(), Store.id.desc()).offset(params.offset).limit(params.limit)
    ).all()
    body = page_response([owner_store_body(store, request_id) for store, request_id in rows], total, params)
    body["asOf"] = iso(as_of)
    return body


@router.get("/api/stores/{storeId}")
def get_my_store(store_id: StoreIdPath, owner: CurrentOwner, db: SessionDep) -> dict:
    store = load_owned_store(db, owner.user_id, store_id, require_approved=False)
    return owner_store_body(store, _approval_request_id(db, store.id))


STORE_ALREADY_REGISTERED_MESSAGE = "이미 등록된 매장입니다. 운영자에게 관리 권한을 문의해 주세요."


def _already_registered() -> ApiError:
    return ApiError(409, ErrorCode.STORE_ALREADY_REGISTERED, STORE_ALREADY_REGISTERED_MESSAGE)


def _verified_address(body: StoreInput) -> str:
    """The canonical address from the address data; field errors name this body's fields.

    `verify_store_address` reports `store.<field>` for the registration body, where the store is
    nested. Here the store is the whole body, so the prefix is dropped.
    """
    try:
        return verify_store_address(body)
    except ApiError as error:
        for field_error in error.field_errors:
            field_error["field"] = field_error["field"].removeprefix("store.")
        raise


def _brn_taken(db, number: str) -> bool:
    return db.scalar(select(Store.id).where(Store.business_registration_number == number)) is not None


def _current_owner(db, owner_id: str) -> None:
    # A shared PK lock sees status changes committed while address verification or a replay
    # waited, and keeps authorization stable until commit. Other stores' approvals and
    # creations can hold the same shared lock; only an account change must wait.
    user = db.scalar(
        select(User).where(User.id == owner_id).with_for_update(read=True)
        .execution_options(populate_existing=True)
    )
    if user is None:
        raise ApiError(401, ErrorCode.SESSION_EXPIRED)
    if user.status != "ACTIVE":
        raise ApiError(403, ErrorCode.ACCOUNT_SUSPENDED)
    if user.role != "OWNER":
        raise ApiError(403, ErrorCode.FORBIDDEN)


# Attempts of the whole create transaction when MySQL picks it as a deadlock victim.
CREATE_STORE_ATTEMPTS = 3


@router.post("/api/stores", status_code=201)
def create_store(body: StoreInput, owner: CsrfOwner, db: SessionDep, key: IdempotencyKey):
    verified: list[str] = []

    def work() -> IdempotentResult:
        # The external address check runs before any write or lock (run_idempotent has just
        # committed, so no transaction is open while it waits). A retry reuses its result.
        if not verified:
            verified.append(_verified_address(body))
        _current_owner(db, owner.user_id)
        if _brn_taken(db, body.businessRegistrationNumber):
            raise _already_registered()
        now = utcnow()
        store = Store(
            owner_id=owner.user_id, name=body.name, industry=body.industry,
            postal_code=body.postalCode, address=verified[0], detail_address=body.detailAddress,
            business_registration_number=body.businessRegistrationNumber,
            phone_number=body.phoneNumber, approval_status=PENDING, created_at=now,
        )
        db.add(store)
        db.flush()  # a concurrent duplicate number fails here on the UNIQUE index
        request = StoreApprovalRequest(store_id=store.id, status=PENDING, submitted_at=now)
        db.add(request)
        db.flush()
        return IdempotentResult(201, owner_store_body(store, request.id))

    def revalidate() -> None:
        _current_owner(db, owner.user_id)
        # The business number is unique: one current row is locked, without a range or JOIN.
        # A transfer after the original creation must not expose its saved response to the
        # former owner, even though that owner's member session is still valid.
        store_owner = db.scalar(
            select(Store.owner_id)
            .where(Store.business_registration_number == body.businessRegistrationNumber)
            .with_for_update(read=True)
        )
        if store_owner != owner.user_id:
            raise ApiError(403, ErrorCode.FORBIDDEN)

    for attempt in range(1, CREATE_STORE_ATTEMPTS + 1):
        try:
            return run_idempotent(
                db=db, principal=owner, key=key, method="POST", path="/api/stores", body=body,
                handler=work, revalidate=revalidate,
            )
        except IntegrityError:
            # run_idempotent rolled back and released the key. Only the business number UNIQUE
            # is a known conflict; anything else stays an internal error.
            if _brn_taken(db, body.businessRegistrationNumber):
                raise _already_registered() from None
            raise
        except OperationalError as error:
            # Requests waiting on the same new business number all hold shared locks on it. If
            # the transaction that inserted it rolls back, they deadlock and MySQL kills all but
            # one (1213). Nothing was kept and the key was released: run the request again,
            # which now sees the winner's row (409) or wins itself.
            if not is_lock_contention(error) or attempt == CREATE_STORE_ATTEMPTS:
                raise
    raise AssertionError("unreachable")


@router.get("/api/stores/{storeId}/management-summary")
def get_management_summary(store_id: StoreIdPath, owner: CurrentOwner, db: SessionDep) -> dict:
    store = load_owned_store(db, owner.user_id, store_id)
    now = utcnow()
    pending = db.scalar(
        select(func.count()).select_from(StoreInvitation)
        .where(StoreInvitation.store_id == store.id, pending_invitation_clause(now))
    )
    # One row per ACTIVE WORKER with a valid grant; `lasting` is 1 if any of them does not
    # end within 24 hours. Workers whose valid grants all end soon are expiring (a subset).
    per_worker = (
        select(
            StoreAccessGrant.worker_id,
            func.max(case((lasting_grant_clause(now), 1), else_=0)).label("lasting"),
        )
        .join(User, User.id == StoreAccessGrant.worker_id)
        .where(StoreAccessGrant.store_id == store.id, valid_grant_clause(now),
               User.role == "WORKER", User.status == "ACTIVE")
        .group_by(StoreAccessGrant.worker_id)
        .subquery()
    )
    active, expiring = db.execute(select(
        func.count(),
        func.coalesce(func.sum(case((per_worker.c.lasting == 0, 1), else_=0)), 0),
    ).select_from(per_worker)).one()
    return {
        "storeId": store.id,
        "pendingInvitationCount": pending,
        "activeWorkerCount": active,
        "expiringWorkerCount": int(expiring),
        "asOf": iso(now),
    }
