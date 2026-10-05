"""Current DB state serialized to the hand-authored Session contract."""
from datetime import datetime

from fastapi import APIRouter
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import SESSION_IDLE_TIMEOUT, CurrentMember, CurrentMemberOrRegistration
from app.db import SessionDep
from app.db.models import AuthSession, Store, User

router = APIRouter(prefix="/api/auth")
STORE_PERMISSIONS = [
    "READ_STORE_STATUS", "MANAGE_STORE", "INVITE_WORKERS", "MANAGE_JOB_POSTINGS", "MANAGE_MANUALS",
]


def identity(email: str) -> dict:
    return {"provider": "GOOGLE", "email": email, "emailVerified": True}


def session_body(db: Session, user: User, expires_at: datetime) -> dict:
    stores = []
    if user.role == "OWNER":
        rows = db.scalars(select(Store).where(Store.owner_id == user.id).order_by(Store.created_at, Store.id))
        stores = [
            {"storeId": s.id, "storeName": s.name, "approvalStatus": s.approval_status,
             "permissions": STORE_PERMISSIONS if s.approval_status == "APPROVED" else ["READ_STORE_STATUS"]}
            for s in rows
        ]
    action = "WORKER_HOME" if user.role == "WORKER" else (
        "OWNER_HOME" if any(s["approvalStatus"] == "APPROVED" for s in stores)
        else "OWNER_APPROVAL_PENDING"
    )
    return {
        "user": {
            "id": user.id, "role": user.role, "name": user.name,
            "identity": identity(user.google_email), "phoneNumber": user.phone_number,
            "profileCompleted": True,
            "permissions": ["BROWSE_JOB_POSTINGS", "APPLY_FOR_JOBS", "READ_OWN_PROFILE"]
            if user.role == "WORKER" else ["READ_OWN_STORE_STATUS"], "stores": stores,
        },
        "expiresAt": expires_at.isoformat(), "nextAction": action,
    }


@router.get("/session")
def current_session(member: CurrentMember, db: SessionDep) -> dict:
    row = db.get(AuthSession, member.session_id)
    return session_body(db, db.get(User, member.user_id), min(row.expires_at, row.last_seen_at + SESSION_IDLE_TIMEOUT))


@router.get("/csrf")
def current_csrf(principal: CurrentMemberOrRegistration, db: SessionDep) -> dict:
    expires = principal.expires_at
    if hasattr(principal, "session_id"):
        row = db.get(AuthSession, principal.session_id)
        expires = min(expires, row.last_seen_at + SESSION_IDLE_TIMEOUT)
    return {"csrfToken": principal.csrf_token, "expiresAt": expires.isoformat()}
