"""Stores a worker can currently use, for the manual and AI question store pickers
(docs/worker-store-selection-design.md).

Regular and temporary grants are grouped into one item per store. A store is listed only while
the worker has a grant valid now and the store is APPROVED with an ACTIVE OWNER; ended access
drops it from the list. The list is a hint: every material request re-checks access through
`app.store_access.require_worker_store_access`.
"""

from typing import Annotated

from fastapi import APIRouter, Path
from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased

from app.auth import CurrentWorker
from app.db import SessionDep, utcnow
from app.db.models import Store, StoreAccessGrant, StoreManual, User
from app.pagination import Pagination
from app.store_access import (
    APPROVED,
    UUID_PATTERN,
    require_worker_store_access,
    store_card,
    valid_grant_clause,
    worker_access_bodies,
    worker_access_bodies_by_store,
)

router = APIRouter(prefix="/api/users/me/stores")

StorePath = Annotated[str, Path(alias="storeId", pattern=UUID_PATTERN)]


def published_version_id(db: Session, store_id: str) -> str | None:
    """The store's current published manual version (store_manuals pointer), None when nothing
    is published yet."""
    return db.scalar(select(StoreManual.current_published_version_id).where(StoreManual.store_id == store_id))


def _item(db: Session, store: Store, access: dict) -> dict:
    return {"store": store_card(store), "access": access, "publishedVersionId": published_version_id(db, store.id)}


@router.get("")
def list_my_stores(worker: CurrentWorker, db: SessionDep, params: Pagination) -> dict:
    now = utcnow()
    owner = aliased(User)
    per_store = (
        select(Store.id.label("store_id"), func.max(StoreAccessGrant.granted_at).label("last_start"))
        .join(StoreAccessGrant, StoreAccessGrant.store_id == Store.id)
        .join(owner, owner.id == Store.owner_id)
        .where(
            StoreAccessGrant.worker_id == worker.user_id, valid_grant_clause(now),
            Store.approval_status == APPROVED, owner.role == "OWNER", owner.status == "ACTIVE",
        )
        .group_by(Store.id)
        .subquery()
    )
    total = db.scalar(select(func.count()).select_from(per_store))
    store_ids = list(db.scalars(
        select(per_store.c.store_id)
        .order_by(per_store.c.last_start.desc(), per_store.c.store_id.desc())
        .offset(params.offset).limit(params.limit)
    ))
    # The page's stores as one set: stores, grants (+ job titles) and published pointers are a
    # fixed number of queries, not three per store.
    stores = {store.id: store for store in db.scalars(select(Store).where(Store.id.in_(store_ids)))}
    access = worker_access_bodies_by_store(db, worker.user_id, store_ids, now)
    published = dict(db.execute(
        select(StoreManual.store_id, StoreManual.current_published_version_id)
        .where(StoreManual.store_id.in_(store_ids))
    ).all()) if store_ids else {}
    items = [
        {"store": store_card(stores[store_id]), "access": access[store_id],
         "publishedVersionId": published.get(store_id)}
        for store_id in store_ids
    ]
    return {"items": items, "page": params.page, "size": params.size, "totalItems": total, "asOf": now.isoformat()}


@router.get("/{storeId}/access")
def get_my_store_access(store_id: StorePath, worker: CurrentWorker, db: SessionDep) -> dict:
    now = utcnow()
    store = require_worker_store_access(db, worker.user_id, store_id, now=now)
    access = worker_access_bodies(db, store.id, [worker.user_id], now)[worker.user_id]
    return _item(db, store, access)
