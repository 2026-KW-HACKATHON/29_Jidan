"""What onboarding needs about a confirmed shift's TEMPORARY access and the store's manual.

The `StoreAccessGrant` projection (status, permissions, body) is app.store_access's; this
module only adds the jobs-side questions on top of it.
"""
from datetime import datetime

from sqlalchemy.orm import Session

from app.db.models import StoreAccessGrant
from app.store_access import grant_status
from app.worker_stores import published_version_id


def grant_is_usable(grant: StoreAccessGrant, at: datetime) -> bool:
    return grant.granted_at <= at and grant_status(grant, at) in ("ACTIVE", "EXPIRING")


def published_manual_version(db: Session, store_id: str) -> str | None:
    """The store's current published manual version id, or None (onboarding NOT_PUBLISHED;
    confirmation never depends on it). One wiring point with worker store selection: the manual
    domain (#118~) connects `app.worker_stores.published_version_id`."""
    return published_version_id(db, store_id)
