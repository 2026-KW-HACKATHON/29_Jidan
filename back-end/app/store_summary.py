"""Invitation and effective access counts from one read snapshot and one clock value."""
from datetime import timedelta
from uuid import UUID

from fastapi import APIRouter
from sqlalchemy import case, func, or_, select

from app.auth import CurrentOwner
from app.db import SessionDep, utcnow
from app.db.models import StoreAccessGrant, StoreInvitation, User
from app.owner_stores import owned_store, read_snapshot

router = APIRouter()


@router.get("/api/stores/{storeId}/management-summary")
def management_summary(storeId: UUID, owner: CurrentOwner, db: SessionDep):
    read_snapshot(db)
    now = utcnow()
    store = owned_store(db, owner.user_id, str(storeId), approved=True)
    pending = db.scalar(select(func.count()).select_from(StoreInvitation).where(
        StoreInvitation.store_id == store.id,
        StoreInvitation.accepted_at.is_(None), StoreInvitation.declined_at.is_(None),
        StoreInvitation.canceled_at.is_(None), StoreInvitation.expires_at > now,
        or_(StoreInvitation.access_expires_at.is_(None), StoreInvitation.access_expires_at > now),
    ))
    workers = db.execute(select(
        StoreAccessGrant.worker_id, func.max(StoreAccessGrant.valid_until),
        func.sum(case((StoreAccessGrant.valid_until.is_(None), 1), else_=0)),
    ).join(User, User.id == StoreAccessGrant.worker_id).where(
        StoreAccessGrant.store_id == store.id, StoreAccessGrant.granted_at <= now,
        StoreAccessGrant.revoked_at.is_(None),
        or_(StoreAccessGrant.valid_until.is_(None), StoreAccessGrant.valid_until > now),
        User.role == "WORKER", User.status == "ACTIVE",
    ).group_by(StoreAccessGrant.worker_id)).all()
    expiring = sum(not unlimited and latest <= now + timedelta(hours=24)
                   for _, latest, unlimited in workers)
    return {"storeId": store.id, "pendingInvitationCount": pending,
            "activeWorkerCount": len(workers), "expiringWorkerCount": expiring,
            "asOf": now.isoformat()}
