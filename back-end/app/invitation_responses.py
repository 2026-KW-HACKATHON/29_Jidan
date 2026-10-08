"""Invitee side of worker invitations: preview, accept and decline (docs/store-invitation-design.md).

The worker signed in with the verified Google e-mail the invitation was sent to acts on it.
`respond_to_invitation` and `preview_body` hold the state transitions; the e-mail link endpoints
here find the invitation by token hash, and the in-app invitation inbox (#115) by id + e-mail,
so both paths share the same records, locks and rules.

Order of checks: unknown or replaced token 404, then e-mail mismatch 403 (nothing about the
invitation or store is revealed), then the store must still be APPROVED with an ACTIVE owner
(403 STORE_APPROVAL_REQUIRED), then the invitation state (409/410).

Locks: store row, then invitation row (the same order as cancel/resend and access revocation);
the transition itself is a conditional UPDATE on "no outcome yet".
"""

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import exists, select, update
from sqlalchemy.orm import Session

from app import notification_events
from app.auth import CurrentWorker, MemberPrincipal
from app.csrf import CsrfWorker
from app.db import SessionDep, utcnow
from app.db.models import Store, StoreAccessGrant, StoreInvitation, User
from app.errors import ErrorCode
from app.invitations import (
    ACCEPT_CONFLICT_MESSAGE,
    DECLINED,
    PENDING,
    error,
    expiry_code,
    hash_invitation_token,
    iso_or_none,
    no_outcome,
    normalize_email,
    refuse_unless_pending,
    regular_grant_clause,
)
from app.store_access import APPROVED, grant_body, valid_grant_clause

router = APIRouter()



def _check_recipient(db: Session, worker_id: str, invitation: StoreInvitation) -> User:
    worker = db.get(User, worker_id)
    if worker is None or not worker.email_verified or normalize_email(worker.google_email) != invitation.invited_email:
        raise error(403, ErrorCode.INVITATION_EMAIL_MISMATCH)
    return worker


def _check_store_operating(db: Session, store: Store) -> None:
    owner = db.get(User, store.owner_id)
    if store.approval_status != APPROVED or owner is None or owner.role != "OWNER" or owner.status != "ACTIVE":
        raise error(403, ErrorCode.STORE_APPROVAL_REQUIRED)


def preview_body(db: Session, worker_id: str, invitation: StoreInvitation) -> dict:
    """Read-only view of a PENDING invitation for its recipient; changes nothing."""
    _check_recipient(db, worker_id, invitation)
    store = db.get(Store, invitation.store_id)
    _check_store_operating(db, store)
    now = utcnow()
    refuse_unless_pending(invitation, now)
    return {
        "invitationId": invitation.id,
        "store": {"id": store.id, "name": store.name},
        "status": PENDING,
        "expiresAt": iso_or_none(invitation.expires_at),
        "accessType": "REGULAR",
        "accessExpiresAt": iso_or_none(invitation.access_expires_at),
    }


def _replay_deadline(invitation: StoreInvitation, now: datetime) -> None:
    """A repeated response by the same worker is only replayed while both deadlines remain."""
    code = expiry_code(invitation, now)
    if code is not None:
        raise error(410, code)


def respond_to_invitation(
    db: Session, worker_id: str, invitation_id: str, action: Literal["ACCEPT", "DECLINE"],
    *, token_hash: str | None = None, commit: bool = True,
    existing_regular: Literal["conflict", "return"] = "conflict",
) -> dict:
    """Accept or decline `invitation_id` for `worker_id`; returns the response body.

    The caller found the invitation (by token, or by id for the inbox) and has not changed
    anything yet. With `token_hash`, a token replaced by a resend while this request waited for
    the lock is 404. Raises the token API's 403/404/409/410 errors (the inbox maps them to its
    own contract). `commit=False` leaves the final commit to the caller, e.g. `run_idempotent`,
    which commits the change together with its stored response.

    `existing_regular` decides an ACCEPT of a PENDING invitation by a worker who already has a
    valid REGULAR grant at the store: "conflict" (token API) is 409 WORKER_ALREADY_HAS_ACCESS;
    "return" (invitation inbox, per its spec) is 200 with that existing grant and changes
    nothing, leaving the invitation PENDING. It is not marked ACCEPTED because the grant came
    from another invitation: recording it as this one's acceptance would make the re-accept rule
    (grant looked up by `invitation_id`) and the history disagree. A TEMPORARY grant never counts.
    """
    store_id = db.scalar(select(StoreInvitation.store_id).where(StoreInvitation.id == invitation_id))
    # End the read snapshot the caller's lookups opened (MySQL REPEATABLE READ). The next
    # statement is the lock, so plain reads after it see what a concurrent response committed
    # while this one waited (e.g. the grant of an acceptance that won). Nothing is written yet,
    # so this is safe inside `run_idempotent` too.
    db.commit()
    # populate_existing: the caller's earlier unlocked read must not mask what the lock sees.
    store = db.execute(
        select(Store).where(Store.id == store_id).with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one()
    invitation = db.execute(
        select(StoreInvitation).where(StoreInvitation.id == invitation_id).with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one()
    if token_hash is not None and invitation.token_hash != token_hash:
        raise error(404, ErrorCode.INVITATION_NOT_FOUND)
    _check_recipient(db, worker_id, invitation)
    _check_store_operating(db, store)
    now = utcnow()
    if action == "ACCEPT":
        result = _accept(db, worker_id, invitation, now, existing_regular=existing_regular)
    else:
        result = _decline(db, worker_id, invitation, now)
    if commit:
        db.commit()
    return result


def _acceptance_body(invitation: StoreInvitation, grant: StoreAccessGrant, now: datetime) -> dict:
    return {
        "invitationId": invitation.id, "storeId": invitation.store_id,
        "workerId": grant.worker_id, "accessGrant": grant_body(grant, now),
    }


def _accept(
    db: Session, worker_id: str, invitation: StoreInvitation, now: datetime,
    *, existing_regular: Literal["conflict", "return"] = "conflict",
) -> dict:
    conflict = error(409, ErrorCode.INVITATION_STATE_CONFLICT, ACCEPT_CONFLICT_MESSAGE)
    if invitation.accepted_at is not None:
        if invitation.accepted_by_worker_id != worker_id:
            raise conflict
        _replay_deadline(invitation, now)
        grant = db.scalar(select(StoreAccessGrant).where(StoreAccessGrant.invitation_id == invitation.id))
        if grant is None or not db.scalar(select(exists().where(
            StoreAccessGrant.id == grant.id, valid_grant_clause(now),
        ))):
            raise conflict  # the owner ended this access; re-accepting never restores it
        return _acceptance_body(invitation, grant, now)
    if invitation.declined_at is not None or invitation.canceled_at is not None:
        raise conflict
    code = expiry_code(invitation, now)
    if code is not None:
        raise error(410, code)
    existing = db.scalar(
        select(StoreAccessGrant).where(
            StoreAccessGrant.store_id == invitation.store_id, StoreAccessGrant.worker_id == worker_id,
            regular_grant_clause(), valid_grant_clause(now),
        ).order_by(StoreAccessGrant.granted_at.desc(), StoreAccessGrant.id.desc()).limit(1)
    )
    if existing is not None:
        if existing_regular == "return":
            return _acceptance_body(invitation, existing, now)  # nothing changes; still PENDING
        raise error(409, ErrorCode.WORKER_ALREADY_HAS_ACCESS)
    changed = db.execute(
        update(StoreInvitation).where(*no_outcome(invitation.id))
        .values(accepted_by_worker_id=worker_id, accepted_at=now)
        .execution_options(synchronize_session=False)
    ).rowcount
    if changed != 1:
        raise conflict
    grant = StoreAccessGrant(
        store_id=invitation.store_id, worker_id=worker_id, invitation_id=invitation.id,
        granted_at=now, valid_until=invitation.access_expires_at,
    )
    db.add(grant)
    db.flush()
    # notification: INVITATION_ACCEPTED (to the store owner; target STORE=invitation.store_id)
    notification_events.invitation_accepted(db, invitation, db.get(Store, invitation.store_id))
    return _acceptance_body(invitation, grant, now)


def _decline(db: Session, worker_id: str, invitation: StoreInvitation, now: datetime) -> dict:
    if invitation.declined_at is not None:
        if invitation.declined_by_worker_id != worker_id:
            raise error(409, ErrorCode.INVITATION_STATE_CONFLICT)
        _replay_deadline(invitation, now)
        return {"invitationId": invitation.id, "status": DECLINED, "declinedAt": iso_or_none(invitation.declined_at)}
    if invitation.accepted_at is not None or invitation.canceled_at is not None:
        raise error(409, ErrorCode.INVITATION_STATE_CONFLICT)
    code = expiry_code(invitation, now)
    if code is not None:
        raise error(410, code)
    changed = db.execute(
        update(StoreInvitation).where(*no_outcome(invitation.id))
        .values(declined_by_worker_id=worker_id, declined_at=now)
        .execution_options(synchronize_session=False)
    ).rowcount
    if changed != 1:
        raise error(409, ErrorCode.INVITATION_STATE_CONFLICT)
    return {"invitationId": invitation.id, "status": DECLINED, "declinedAt": iso_or_none(now)}


# --- invitee: token endpoints ----------------------------------------------------------------

class TokenBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: Annotated[str, Field(min_length=43, max_length=512, pattern=r"^[A-Za-z0-9_-]+$", strict=True)]

    def __repr__(self) -> str:
        return "TokenBody(token=***)"

    __str__ = __repr__


def _invitation_for_token(db: Session, token: str) -> StoreInvitation:
    invitation = db.scalar(select(StoreInvitation).where(StoreInvitation.token_hash == hash_invitation_token(token)))
    if invitation is None:
        raise error(404, ErrorCode.INVITATION_NOT_FOUND)  # unknown or replaced by a resend
    return invitation


@router.post("/api/store-invitations/preview")
def preview_invitation(body: TokenBody, worker: CurrentWorker, db: SessionDep) -> dict:
    return preview_body(db, worker.user_id, _invitation_for_token(db, body.token))


def _respond_with_token(body: TokenBody, worker: MemberPrincipal, db: Session, action) -> dict:
    invitation = _invitation_for_token(db, body.token)
    # Checked before any lock so a mismatched account learns nothing more than "not yours".
    _check_recipient(db, worker.user_id, invitation)
    return respond_to_invitation(
        db, worker.user_id, invitation.id, action, token_hash=invitation.token_hash,
    )


@router.post("/api/store-invitations/accept")
def accept_invitation(body: TokenBody, worker: CsrfWorker, db: SessionDep) -> dict:
    return _respond_with_token(body, worker, db, "ACCEPT")


@router.post("/api/store-invitations/decline")
def decline_invitation(body: TokenBody, worker: CsrfWorker, db: SessionDep) -> dict:
    return _respond_with_token(body, worker, db, "DECLINE")
