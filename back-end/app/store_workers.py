"""Owner worker management: list, detail and ending a worker's access (docs/store-worker-design.md).

A worker appears once per store however many grants (REGULAR from invitations, TEMPORARY from
confirmed shifts) they have; `accessGrants` keeps the individual history. Only the name is
projected from the account.

Ending access revokes every grant of the worker at this store that has not ended yet, cancels
the store's still-acceptable invitations to the worker's verified e-mail and discards their
unsent mail, all in one transaction under the store lock (the same lock order as invitation
accept/cancel/resend), so an old pending link cannot recreate access. Expired grants and the
first `revokedAt` of revoked ones are kept as history.
"""

from typing import Annotated, Literal

from fastapi import APIRouter, Path
from fastapi.responses import Response
from sqlalchemy import and_, case, func, or_, select

from app.auth import CurrentOwner
from app.csrf import CsrfOwner
from app.db import SessionDep, utcnow
from app.db.keyed import update_by_key
from app.db.models import StoreAccessGrant, StoreInvitation, User
from app.email_match import email_is
from app.errors import ApiError, ErrorCode
from app.invitation_mail import discard_queued_mail
from app.invitations import normalize_email
from app.pagination import Pagination, page_response
from app.store_access import (
    UUID_PATTERN,
    StoreIdPath,
    lasting_grant_clause,
    load_owned_store,
    normalize_uuid,
    pending_invitation_clause,
    valid_grant_clause,
    worker_access_bodies,
)

router = APIRouter()

WorkerIdPath = Annotated[str, Path(alias="workerId", pattern=UUID_PATTERN)]
WORKER_NOT_FOUND_MESSAGE = "해당 매장의 근무자를 찾을 수 없습니다."


def _worker_not_found() -> ApiError:
    return ApiError(404, ErrorCode.STORE_WORKER_NOT_FOUND, WORKER_NOT_FOUND_MESSAGE)


@router.get("/api/stores/{storeId}/workers")
def list_store_workers(
    store_id: StoreIdPath, owner: CurrentOwner, db: SessionDep, params: Pagination,
    view: Literal["ACTIVE", "ENDED", "ALL"] = "ACTIVE",
) -> dict:
    store = load_owned_store(db, owner.user_id, store_id)
    now = utcnow()
    valid = valid_grant_clause(now)
    # One row per worker with history here: latest start, any valid grant, any lasting valid grant.
    per_worker = (
        select(
            StoreAccessGrant.worker_id.label("worker_id"),
            func.max(StoreAccessGrant.granted_at).label("last_start"),
            func.max(case((valid, 1), else_=0)).label("valid"),
            func.max(case((and_(valid, lasting_grant_clause(now)), 1), else_=0)).label("lasting"),
        )
        .where(StoreAccessGrant.store_id == store.id)
        .group_by(StoreAccessGrant.worker_id)
        .subquery()
    )
    active, expiring, ended = db.execute(select(
        func.coalesce(func.sum(per_worker.c.valid), 0),
        func.coalesce(func.sum(case((and_(per_worker.c.valid == 1, per_worker.c.lasting == 0), 1), else_=0)), 0),
        func.coalesce(func.sum(case((per_worker.c.valid == 0, 1), else_=0)), 0),
    )).one()
    active, expiring, ended = int(active), int(expiring), int(ended)
    query = select(per_worker.c.worker_id)
    if view == "ACTIVE":
        query = query.where(per_worker.c.valid == 1)
    elif view == "ENDED":
        query = query.where(per_worker.c.valid == 0)
    worker_ids = list(db.scalars(
        query.order_by(per_worker.c.last_start.desc(), per_worker.c.worker_id.desc())
        .offset(params.offset).limit(params.limit)
    ))
    bodies = worker_access_bodies(db, store.id, worker_ids, now)
    total = {"ACTIVE": active, "ENDED": ended, "ALL": active + ended}[view]
    body = page_response([bodies[worker_id] for worker_id in worker_ids], total, params)
    body.update({"asOf": now.isoformat(), "activeCount": active, "expiringCount": expiring, "endedCount": ended})
    return body


@router.get("/api/stores/{storeId}/workers/{workerId}")
def get_store_worker(
    store_id: StoreIdPath, worker_id: WorkerIdPath, owner: CurrentOwner, db: SessionDep,
) -> dict:
    store = load_owned_store(db, owner.user_id, store_id)
    worker_id = normalize_uuid(worker_id)
    body = worker_access_bodies(db, store.id, [worker_id], utcnow()).get(worker_id)
    if body is None:
        raise _worker_not_found()  # no access history at this store, whoever the user is
    return body


@router.delete("/api/stores/{storeId}/workers/{workerId}/access", status_code=204)
def revoke_store_worker_access(
    store_id: StoreIdPath, worker_id: WorkerIdPath, owner: CsrfOwner, db: SessionDep,
) -> Response:
    db.commit()  # end the authentication read snapshot; the store lock comes first
    store = load_owned_store(db, owner.user_id, store_id, lock=True)
    worker_id = normalize_uuid(worker_id)
    has_history = db.scalar(select(StoreAccessGrant.id).where(
        StoreAccessGrant.store_id == store.id, StoreAccessGrant.worker_id == worker_id,
    ).limit(1))
    if has_history is None:
        raise _worker_not_found()
    now = utcnow()
    # Plain reads find the rows; every change is a primary-key UPDATE that re-checks its
    # condition. Range UPDATE / FOR UPDATE on the (store_id, worker_id) grant index or the
    # (store_id, invited_email) invitation index would lock the gap up to the next key, which
    # can belong to another store whose grants or invitations are being inserted. The store
    # lock above serializes everything that changes this store's grants and invitations, and
    # the snapshot started after it, so these reads are current.
    open_grants = list(db.scalars(select(StoreAccessGrant.id).where(
        StoreAccessGrant.store_id == store.id,
        StoreAccessGrant.worker_id == worker_id,
        StoreAccessGrant.revoked_at.is_(None),
        or_(StoreAccessGrant.valid_until.is_(None), StoreAccessGrant.valid_until > now),
    )))
    if open_grants:
        # A grant that has not started yet ends at its own start (revoked_at >= granted_at).
        update_by_key(db, StoreAccessGrant, open_grants, {
            "revoked_at": case((StoreAccessGrant.granted_at > now, StoreAccessGrant.granted_at), else_=now),
        }, StoreAccessGrant.revoked_at.is_(None))
    email = db.scalar(select(User.google_email).where(User.id == worker_id))
    if email is not None:
        pending_ids = list(db.scalars(select(StoreInvitation.id).where(
            StoreInvitation.store_id == store.id,
            email_is(StoreInvitation.invited_email, normalize_email(email)),
            pending_invitation_clause(now),
        )))
        if pending_ids:
            update_by_key(db, StoreInvitation, pending_ids, {"canceled_at": now}, pending_invitation_clause(now))
            for invitation_id in pending_ids:
                discard_queued_mail(db, invitation_id, now=now)
    db.commit()
    return Response(status_code=204)
