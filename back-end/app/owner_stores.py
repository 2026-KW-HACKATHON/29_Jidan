"""Owner store reads and ownership checks against current database state."""
from uuid import UUID

from fastapi import APIRouter
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.auth import CurrentOwner
from app.auth_views import STORE_PERMISSIONS
from app.csrf import CsrfOwner
from app.db import SessionDep, utcnow
from app.db.models import Store, StoreApprovalRequest, User
from app.errors import ApiError, ErrorCode
from app.idempotency import IdempotencyKey, IdempotentResult, run_idempotent
from app.pagination import Pagination, page_response
from app.registration_inputs import StoreInput
from app.store_address import verify_store_address

router = APIRouter()


def read_snapshot(db):
    # sqlite3's legacy mode does not BEGIN for SELECT; explicitly start a snapshot.
    connection = db.connection()
    if connection.dialect.name == "sqlite" and not connection.connection.driver_connection.in_transaction:
        connection.exec_driver_sql("BEGIN")


def owned_store(db, owner_id, store_id, *, approved=False):
    store = db.scalar(select(Store).where(Store.id == store_id, Store.owner_id == owner_id))
    if store is None:
        raise ApiError(404, ErrorCode.STORE_NOT_FOUND)
    if approved and store.approval_status != "APPROVED":
        raise ApiError(403, ErrorCode.STORE_APPROVAL_REQUIRED)
    return store


def store_body(store):
    return {
        "id": store.id, "name": store.name, "industry": store.industry,
        "postalCode": store.postal_code, "address": store.address,
        "businessRegistrationNumber": store.business_registration_number,
        "phoneNumber": store.phone_number,
        **({"detailAddress": store.detail_address} if store.detail_address is not None else {}),
    }


def owner_store_body(store, approval):
    return {
        **store_body(store), "approvalRequestId": approval.id,
        "approvalStatus": store.approval_status, "createdAt": store.created_at.isoformat(),
        "approvedAt": store.approved_at.isoformat() if store.approved_at else None,
        "permissions": STORE_PERMISSIONS if store.approval_status == "APPROVED" else ["READ_STORE_STATUS"],
    }


@router.get("/api/owners/me/stores")
def list_stores(owner: CurrentOwner, db: SessionDep, params: Pagination):
    read_snapshot(db)
    as_of = utcnow()
    total = db.scalar(select(func.count()).select_from(Store).where(Store.owner_id == owner.user_id))
    rows = db.execute(select(Store, StoreApprovalRequest).join(StoreApprovalRequest)
                      .where(Store.owner_id == owner.user_id)
                      .order_by(Store.created_at.desc(), Store.id.desc())
                      .offset(params.offset).limit(params.limit)).all()
    return {**page_response([owner_store_body(s, a) for s, a in rows], total, params),
            "asOf": as_of.isoformat()}


@router.get("/api/stores/{storeId}")
def get_store(storeId: UUID, owner: CurrentOwner, db: SessionDep):
    read_snapshot(db)
    store = owned_store(db, owner.user_id, str(storeId))
    approval = db.scalar(select(StoreApprovalRequest).where(StoreApprovalRequest.store_id == store.id))
    return owner_store_body(store, approval)


def current_owner(db, owner_id):
    user = db.scalar(select(User).where(User.id == owner_id).with_for_update()
                     .execution_options(populate_existing=True))
    if user is None:
        raise ApiError(401, ErrorCode.SESSION_EXPIRED)
    if user.status != "ACTIVE":
        raise ApiError(403, ErrorCode.ACCOUNT_SUSPENDED)
    if user.role != "OWNER":
        raise ApiError(403, ErrorCode.FORBIDDEN)
    return user


@router.post("/api/stores", status_code=201)
def create_store(body: StoreInput, owner: CsrfOwner, db: SessionDep, key: IdempotencyKey):
    def work():
        try:
            address = verify_store_address(body)
        except ApiError as exc:
            # The shared registration validator uses store.*; this endpoint's body is flat.
            for error in exc.field_errors:
                error["field"] = error["field"].removeprefix("store.")
            raise
        current_owner(db, owner.user_id)
        if db.scalar(select(Store.id).where(
            Store.business_registration_number == body.businessRegistrationNumber,
        )) is not None:
            raise ApiError(409, ErrorCode.STORE_ALREADY_REGISTERED)
        store = Store(owner_id=owner.user_id, name=body.name, industry=body.industry,
                      postal_code=body.postalCode, address=address, detail_address=body.detailAddress,
                      business_registration_number=body.businessRegistrationNumber,
                      phone_number=body.phoneNumber, approval_status="PENDING")
        db.add(store)
        db.flush()
        approval = StoreApprovalRequest(store_id=store.id, status="PENDING")
        db.add(approval)
        db.flush()
        return IdempotentResult(201, owner_store_body(store, approval))

    def revalidate():
        current_owner(db, owner.user_id)
        store = db.scalar(select(Store).where(
            Store.business_registration_number == body.businessRegistrationNumber,
            Store.owner_id == owner.user_id,
        ).with_for_update().execution_options(populate_existing=True))
        if store is None:
            raise ApiError(403, ErrorCode.FORBIDDEN)

    try:
        return run_idempotent(db=db, principal=owner, key=key, method="POST", path="/api/stores",
                              body=body, handler=work, revalidate=revalidate)
    except IntegrityError:
        # Known UNIQUE conflict only; unrelated persistence failures remain internal errors.
        if db.scalar(select(Store.id).where(
            Store.business_registration_number == body.businessRegistrationNumber,
        )) is not None:
            raise ApiError(409, ErrorCode.STORE_ALREADY_REGISTERED) from None
        raise ApiError(500, ErrorCode.INTERNAL_ERROR) from None
