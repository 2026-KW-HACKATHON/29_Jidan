"""A worker's saved (관심) stores (docs/favorite-store-design.md, docs/erd/favorite-store.md).

Saving creates no access, invitation or application. Only APPROVED stores can be saved and only
APPROVED stores are listed; a store that is not (or no longer) approved is the same 404 as a
missing one, so the API never reveals unapproved stores.
"""
import re
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.auth import CurrentWorker
from app.csrf import CsrfWorker
from app.db import SessionDep, utcnow
from app.db.models import FavoriteStore, Store
from app.errors import ApiError, ErrorCode
from app.idempotency import IdempotencyKey, IdempotentResult, run_idempotent
from app.pagination import Pagination
from app.store_address import SERVICE_NEIGHBORHOOD

router = APIRouter(prefix="/api/users/me/favorite-stores")

_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


class FavoriteStoreSave(BaseModel):
    """The empty `FavoriteStoreSave` body; unknown properties are 422."""

    model_config = ConfigDict(extra="forbid")


def store_id(storeId: str) -> str:
    """The path UUID in canonical lower case (stored IDs are lower case)."""
    if not _UUID.match(storeId):
        raise ApiError(422, ErrorCode.VALIDATION_ERROR, field_errors=[
            {"field": "storeId", "code": "INVALID_FORMAT", "message": "UUID 형식이어야 합니다."},
        ])
    return storeId.lower()


StoreId = Annotated[str, Depends(store_id)]


def store_card(store: Store) -> dict[str, Any]:
    """The public `JobStoreCard`: no owner, phone, business number or detail address."""
    return {
        "id": store.id, "name": store.name, "industry": store.industry, "address": store.address,
        "neighborhood": SERVICE_NEIGHBORHOOD,
    }


def favorite_body(store: Store, favorite: FavoriteStore) -> dict[str, Any]:
    return {"store": store_card(store), "savedAt": favorite.saved_at.isoformat()}


def _approved_store(db: Session, store_uuid: str) -> Store:
    store = db.scalar(select(Store).where(Store.id == store_uuid, Store.approval_status == "APPROVED"))
    if store is None:
        raise ApiError(404, ErrorCode.RESOURCE_NOT_FOUND)
    return store


def _insert_unless_saved(db: Session, values: dict[str, Any]):
    # Not INSERT IGNORE: that would also turn FK violations into warnings on MySQL.
    if db.get_bind().dialect.name in ("mysql", "mariadb"):
        statement = mysql_insert(FavoriteStore).values(**values)
        return statement.on_duplicate_key_update(saved_at=FavoriteStore.saved_at)  # keep the first savedAt
    return sqlite_insert(FavoriteStore).values(**values).on_conflict_do_nothing()


@router.put("/{storeId}")
def save_favorite_store(
    member: CsrfWorker, db: SessionDep, key: IdempotencyKey, body: FavoriteStoreSave, store_uuid: StoreId,
):
    def work() -> IdempotentResult:
        store = _approved_store(db, store_uuid)
        # Concurrent saves of the same pair meet on the primary key; the first savedAt wins.
        db.execute(_insert_unless_saved(db, {"worker_id": member.user_id, "store_id": store.id, "saved_at": utcnow()}))
        # A locking read: under MySQL REPEATABLE READ a plain SELECT would read the snapshot taken
        # before a concurrent save committed and miss the row the upsert just collided with.
        favorite = db.scalar(
            select(FavoriteStore)
            .where(FavoriteStore.worker_id == member.user_id, FavoriteStore.store_id == store.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return IdempotentResult(200, favorite_body(store, favorite))

    return run_idempotent(
        db=db, principal=member, key=key, method="PUT", path=f"{router.prefix}/{store_uuid}", body=body,
        handler=work, revalidate=lambda: _approved_store(db, store_uuid),
    )


@router.delete("/{storeId}", status_code=204)
def remove_favorite_store(member: CsrfWorker, db: SessionDep, key: IdempotencyKey, store_uuid: StoreId):
    # No store lookup at all: removing works for stores that lost approval and never reveals
    # whether a store exists. A missing relation is already the requested state (204).
    def work() -> IdempotentResult:
        db.execute(delete(FavoriteStore).where(
            FavoriteStore.worker_id == member.user_id, FavoriteStore.store_id == store_uuid,
        ))
        return IdempotentResult(204)

    return run_idempotent(
        db=db, principal=member, key=key, method="DELETE", path=f"{router.prefix}/{store_uuid}", body=None,
        handler=work,
    )


@router.get("")
def list_my_favorite_stores(member: CurrentWorker, db: SessionDep, params: Pagination) -> dict:
    # Only APPROVED stores count, so the list and totalItems (the home count) agree. The store ID
    # breaks savedAt ties (the relation has no ID of its own) to keep pages stable.
    as_of = utcnow()
    conditions = (
        FavoriteStore.worker_id == member.user_id, FavoriteStore.saved_at <= as_of,
        Store.approval_status == "APPROVED",
    )
    total = db.scalar(
        select(func.count()).select_from(FavoriteStore)
        .join(Store, Store.id == FavoriteStore.store_id).where(*conditions)
    )
    rows = db.execute(
        select(FavoriteStore, Store).join(Store, Store.id == FavoriteStore.store_id).where(*conditions)
        .order_by(FavoriteStore.saved_at.desc(), FavoriteStore.store_id.desc())
        .offset(params.offset).limit(params.limit)
    ).all()
    return {
        "items": [favorite_body(store, favorite) for favorite, store in rows],
        "page": params.page,
        "size": params.size,
        "totalItems": total,
        "asOf": as_of.isoformat(),
    }
