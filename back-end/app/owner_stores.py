"""Owner store reads and ownership checks against current database state."""
from uuid import UUID

from fastapi import APIRouter
from sqlalchemy import func, select

from app.auth import CurrentOwner
from app.auth_views import STORE_PERMISSIONS
from app.db import SessionDep, utcnow
from app.db.models import Store, StoreApprovalRequest
from app.errors import ApiError, ErrorCode
from app.pagination import Pagination, page_response

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
