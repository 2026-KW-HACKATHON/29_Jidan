"""Store ownership and approval checks shared by every store-scoped endpoint.

Owner endpoints (`/api/stores/{storeId}/...`) must re-check on every request that the session
owner owns the store and, for operating features, that it is APPROVED (docs/auth-design.md).
`role=OWNER` alone never opens a store. Use:

    store = load_owned_store(db, owner.user_id, store_id)                 # APPROVED only
    store = load_owned_store(db, owner.user_id, store_id, require_approved=False)  # status pages
    store = load_owned_store(db, owner.user_id, store_id, lock=True)      # before a state change

A missing store and another owner's store are the same 404, so existence is never revealed.
"""

import re
from datetime import datetime, timedelta
from typing import Annotated

from fastapi import Path
from sqlalchemy import ColumnElement, and_, or_, select
from sqlalchemy.orm import Session

from app.db import utcnow
from app.db.models import (
    JobPosting,
    ShiftAssignment,
    Store,
    StoreAccessGrant,
    StoreInvitation,
    User,
)
from app.errors import ApiError, ErrorCode

APPROVED = "APPROVED"
PENDING = "PENDING"

UUID_PATTERN = r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
_UUID = re.compile(UUID_PATTERN)

STORE_NOT_FOUND_MESSAGE = "매장을 찾을 수 없습니다."
STORE_APPROVAL_REQUIRED_MESSAGE = "매장 운영 승인 후 이용할 수 있습니다."

# `{storeId}` path parameter: a malformed UUID is 422 VALIDATION_ERROR (field `storeId`), as
# the spec's 422 for "UUID 형식 오류". Pass it through `normalize_uuid` before querying.
StoreIdPath = Annotated[str, Path(alias="storeId", pattern=UUID_PATTERN)]


def normalize_uuid(value: str) -> str:
    """Lower-case a client supplied UUID: ids are stored lower-case and SQLite compares exactly."""
    return value.lower()


def is_uuid(value: str) -> bool:
    return bool(_UUID.match(value))


def load_owned_store(
    db: Session,
    owner_id: str,
    store_id: str,
    *,
    require_approved: bool = True,
    lock: bool = False,
    not_found: ErrorCode = ErrorCode.STORE_NOT_FOUND,
) -> Store:
    """The store `store_id` if `owner_id` owns it; otherwise 404 (`not_found`, default
    STORE_NOT_FOUND; job postings use RESOURCE_NOT_FOUND per their spec).

    * `require_approved=True` (operating features): a PENDING store of this owner is
      403 STORE_APPROVAL_REQUIRED. Ownership is checked first, so another owner's pending
      store is still 404.
    * `lock=True`: `SELECT ... FOR UPDATE` on the store row (MySQL), held until the caller's
      transaction ends. Take it before changing rows that depend on the store's state so a
      concurrent change of the same store is serialized. Under REPEATABLE READ make the lock
      the first statement of the transaction (`db.commit()` first if the request already read
      something; `run_idempotent` handlers already start fresh): plain reads after the lock then
      see what the request that held it committed.
    """
    statement = select(Store).where(Store.id == normalize_uuid(store_id), Store.owner_id == owner_id)
    if lock:
        # populate_existing: an earlier unlocked read in this session must not mask the locked row.
        statement = statement.with_for_update().execution_options(populate_existing=True)
    store = db.execute(statement).scalar_one_or_none()
    if store is None:
        message = STORE_NOT_FOUND_MESSAGE if not_found == ErrorCode.STORE_NOT_FOUND else None
        raise ApiError(404, not_found, message)
    if require_approved and store.approval_status != APPROVED:
        raise ApiError(403, ErrorCode.STORE_APPROVAL_REQUIRED, STORE_APPROVAL_REQUIRED_MESSAGE)
    return store


# --- access grants and invitations at a point in time ----------------------------------------

# A valid grant ending within this window is EXPIRING (docs/store-worker-design.md).
EXPIRING_WINDOW = timedelta(hours=24)


def valid_grant_clause(now: datetime) -> ColumnElement[bool]:
    """SQL condition for a store access grant usable at `now`.

    Valid when `granted_at <= now`, `valid_until` is NULL or after `now` (the end is
    exclusive) and it was never revoked. Account status and store approval are separate checks.
    """
    return and_(
        StoreAccessGrant.granted_at <= now,
        or_(StoreAccessGrant.valid_until.is_(None), StoreAccessGrant.valid_until > now),
        StoreAccessGrant.revoked_at.is_(None),
    )


def lasting_grant_clause(now: datetime) -> ColumnElement[bool]:
    """A valid grant that does not end within `EXPIRING_WINDOW` (open-ended or later)."""
    return or_(
        StoreAccessGrant.valid_until.is_(None),
        StoreAccessGrant.valid_until > now + EXPIRING_WINDOW,
    )


def pending_invitation_clause(now: datetime) -> ColumnElement[bool]:
    """SQL condition for an invitation that can still be accepted at `now` (the ACTIVE tab).

    No outcome recorded, and neither the 7 day link nor the optional access period has ended;
    both ends are exclusive. Stored rows have no status column: EXPIRED is computed from time.
    """
    return and_(
        StoreInvitation.accepted_at.is_(None),
        StoreInvitation.declined_at.is_(None),
        StoreInvitation.canceled_at.is_(None),
        StoreInvitation.expires_at > now,
        or_(StoreInvitation.access_expires_at.is_(None), StoreInvitation.access_expires_at > now),
    )


# --- access grant projection (shared by invitation acceptance and worker management) --------

GRANT_PERMISSIONS = ["READ_MANUALS", "READ_CHECKLISTS", "USE_AI_QA"]


def grant_type(grant: StoreAccessGrant) -> str:
    return "REGULAR" if grant.invitation_id is not None else "TEMPORARY"


def grant_status(grant: StoreAccessGrant, now: datetime) -> str:
    """REVOKED (manual end wins), EXPIRED (`valid_until <= now`), EXPIRING (ends within 24h) or ACTIVE."""
    if grant.revoked_at is not None:
        return "REVOKED"
    if grant.valid_until is not None and grant.valid_until <= now:
        return "EXPIRED"
    if grant.valid_until is not None and grant.valid_until <= now + EXPIRING_WINDOW:
        return "EXPIRING"
    return "ACTIVE"


def grant_body(grant: StoreAccessGrant, now: datetime, *, duty_label: str | None = None) -> dict:
    """The `StoreAccessGrant` schema; permissions only while the grant is usable.

    `duty_label` overrides the stored label (TEMPORARY grants show their job posting's title).
    """
    status = grant_status(grant, now)
    return {
        "id": grant.id,
        "type": grant_type(grant),
        "dutyLabel": duty_label if duty_label is not None else grant.duty_label,
        "startedAt": grant.granted_at.isoformat(),
        "validUntil": None if grant.valid_until is None else grant.valid_until.isoformat(),
        "revokedAt": None if grant.revoked_at is None else grant.revoked_at.isoformat(),
        "status": status,
        "permissions": list(GRANT_PERMISSIONS) if status in ("ACTIVE", "EXPIRING") else [],
    }


# --- a worker's access to one store (owner worker views, worker store selection, materials) ---

WORKER_ACTIVE = "ACTIVE"
WORKER_EXPIRING = "EXPIRING"
WORKER_ENDED = "ENDED"
# Every store is verified to be in the service area before approval (app.store_address).
SERVICE_NEIGHBORHOOD = "월계1동"


def store_card(store: Store) -> dict:
    """The `JobStoreCard` schema."""
    return {
        "id": store.id, "name": store.name, "industry": store.industry, "address": store.address,
        "neighborhood": SERVICE_NEIGHBORHOOD,
    }


def worker_access_status(grants: list[StoreAccessGrant], now: datetime) -> str:
    """ACTIVE if any usable grant is ACTIVE, EXPIRING if every usable grant ends within 24h,
    ENDED without a usable grant. A grant that has not started yet is not usable."""
    usable = [
        grant_status(grant, now) for grant in grants
        if grant.granted_at <= now and grant_status(grant, now) in ("ACTIVE", "EXPIRING")
    ]
    if not usable:
        return WORKER_ENDED
    return WORKER_ACTIVE if "ACTIVE" in usable else WORKER_EXPIRING


def _duty_labels(db: Session, grants: list[StoreAccessGrant]) -> dict[str, str]:
    """TEMPORARY grants without a stored label show the title of the confirmed job posting."""
    assignment_ids = [g.assignment_id for g in grants if g.assignment_id is not None and g.duty_label is None]
    if not assignment_ids:
        return {}
    titles = dict(db.execute(
        select(ShiftAssignment.id, JobPosting.title)
        .join(JobPosting, JobPosting.id == ShiftAssignment.job_id)
        .where(ShiftAssignment.id.in_(assignment_ids))
    ).all())
    return {g.id: titles[g.assignment_id] for g in grants if g.assignment_id in titles}


def worker_access_bodies(
    db: Session, store_id: str, worker_ids: list[str], now: datetime,
) -> dict[str, dict]:
    """`StoreWorkerAccess` bodies for workers with access history at the store, by worker id.

    Grants are newest first (startedAt, id descending); workers without history are absent.
    Only the name is projected: no e-mail, phone, birthday or profile.
    """
    if not worker_ids:
        return {}
    grants = db.scalars(
        select(StoreAccessGrant)
        .where(StoreAccessGrant.store_id == store_id, StoreAccessGrant.worker_id.in_(worker_ids))
        .order_by(StoreAccessGrant.granted_at.desc(), StoreAccessGrant.id.desc())
    ).all()
    names = dict(db.execute(select(User.id, User.name).where(User.id.in_(worker_ids))).all())
    labels = _duty_labels(db, list(grants))
    by_worker: dict[str, list[StoreAccessGrant]] = {}
    for grant in grants:
        by_worker.setdefault(grant.worker_id, []).append(grant)
    return {
        worker_id: _access_body(worker_id, names[worker_id], store_id, worker_grants, labels, now)
        for worker_id, worker_grants in by_worker.items()
    }


def worker_access_bodies_by_store(
    db: Session, worker_id: str, store_ids: list[str], now: datetime,
) -> dict[str, dict]:
    """The same `StoreWorkerAccess` body as `worker_access_bodies`, for one worker at many
    stores, keyed by store id: a fixed number of queries whatever the number of stores."""
    if not store_ids:
        return {}
    grants = db.scalars(
        select(StoreAccessGrant)
        .where(StoreAccessGrant.worker_id == worker_id, StoreAccessGrant.store_id.in_(store_ids))
        .order_by(StoreAccessGrant.granted_at.desc(), StoreAccessGrant.id.desc())
    ).all()
    name = db.scalar(select(User.name).where(User.id == worker_id))
    labels = _duty_labels(db, list(grants))
    by_store: dict[str, list[StoreAccessGrant]] = {}
    for grant in grants:
        by_store.setdefault(grant.store_id, []).append(grant)
    return {
        store_id: _access_body(worker_id, name, store_id, store_grants, labels, now)
        for store_id, store_grants in by_store.items()
    }


def _access_body(worker_id: str, name: str, store_id: str, grants: list[StoreAccessGrant],
                 labels: dict[str, str], now: datetime) -> dict:
    status = worker_access_status(grants, now)
    return {
        "workerId": worker_id,
        "name": name,
        "storeId": store_id,
        "accessStatus": status,
        "accessGrants": [grant_body(g, now, duty_label=labels.get(g.id)) for g in grants],
        "permissions": list(GRANT_PERMISSIONS) if status != WORKER_ENDED else [],
        "asOf": now.isoformat(),
    }


def store_operating(db: Session, store: Store) -> bool:
    """APPROVED and run by an ACTIVE OWNER. A worker with a valid grant to a store that is not
    operating (approval lost or owner suspended) is told 403 STORE_APPROVAL_REQUIRED by the
    material endpoints instead of the 404 that hides stores without access."""
    owner = db.get(User, store.owner_id)
    return (store.approval_status == APPROVED and owner is not None and owner.role == "OWNER"
            and owner.status == "ACTIVE")


def has_worker_store_access(db: Session, worker_id: str, store_id: str, now: datetime) -> bool:
    """True if `worker_id` may read `store_id`'s materials at `now`.

    Re-checked on every material request (manual, checklist, AI Q&A) and during long responses:
    the worker account is an ACTIVE WORKER, the store is APPROVED with an ACTIVE OWNER, and at
    least one grant is valid at `now` (started, not ended, not revoked; REGULAR or TEMPORARY).
    """
    worker = db.get(User, worker_id)
    if worker is None or worker.role != "WORKER" or worker.status != "ACTIVE":
        return False
    store = db.get(Store, normalize_uuid(store_id))
    if store is None or store.approval_status != APPROVED:
        return False
    owner = db.get(User, store.owner_id)
    if owner is None or owner.role != "OWNER" or owner.status != "ACTIVE":
        return False
    return bool(db.scalar(select(StoreAccessGrant.id).where(
        StoreAccessGrant.store_id == store.id, StoreAccessGrant.worker_id == worker_id,
        valid_grant_clause(now),
    ).limit(1)))


def require_worker_store_access(
    db: Session,
    worker_id: str,
    store_id: str,
    *,
    now: datetime | None = None,
    status_code: int = 404,
    code: ErrorCode = ErrorCode.RESOURCE_NOT_FOUND,
) -> Store:
    """The store if `has_worker_store_access`, else ApiError (default 404 RESOURCE_NOT_FOUND,
    which hides whether the store exists). Pass the time of the check as `now` when a long
    response re-validates; the default is the current time."""
    if not has_worker_store_access(db, worker_id, store_id, now or utcnow()):
        message = "리소스를 찾을 수 없습니다." if code == ErrorCode.RESOURCE_NOT_FOUND else None
        raise ApiError(status_code, code, message)
    return db.get(Store, normalize_uuid(store_id))
