"""In-app invitation inbox for workers (docs/invitation-inbox-design.md).

A worker reaches invitations addressed to their verified Google e-mail without the e-mail
link, e.g. from a STORE_INVITED notification. Lookups are by invitation id *and* the session's
normalized verified e-mail, so another person's invitation is the same 404 as a missing one.

Responses reuse the token API's transitions (`app.invitation_responses.respond_to_invitation`:
same records, locks and rules). Only the error contract differs (openapi): a mismatched
address is 404 RESOURCE_NOT_FOUND, a completed invitation is 409 INVITATION_NOT_PENDING and a
reached deadline is 409 INVITATION_EXPIRED / ACCESS_PERIOD_ENDED instead of 410. Accepting
while a valid REGULAR grant already exists returns that grant (200) without creating another
or changing the invitation, as the inbox spec says; the token API answers 409 instead.
"""

from typing import Annotated, Literal

from fastapi import APIRouter, Path
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import CurrentWorker
from app.csrf import CsrfWorker
from app.db import SessionDep, utcnow
from app.db.models import Store, StoreInvitation, User
from app.email_match import email_is
from app.errors import ApiError, ErrorCode
from app.idempotency import IdempotencyKey, IdempotentResult, run_idempotent
from app.invitation_responses import respond_to_invitation
from app.invitations import invitation_status, iso_or_none, normalize_email
from app.pagination import Pagination
from app.store_access import UUID_PATTERN, normalize_uuid, pending_invitation_clause, store_card

router = APIRouter(prefix="/api/users/me/store-invitations")

InvitationPath = Annotated[str, Path(alias="invitationId", pattern=UUID_PATTERN)]
NOT_FOUND_MESSAGE = "리소스를 찾을 수 없습니다."

# Token API error -> inbox error (status, code). Anything else passes through unchanged.
_INBOX_ERRORS = {
    ErrorCode.INVITATION_NOT_FOUND: (404, ErrorCode.RESOURCE_NOT_FOUND),
    ErrorCode.INVITATION_EMAIL_MISMATCH: (404, ErrorCode.RESOURCE_NOT_FOUND),
    ErrorCode.INVITATION_STATE_CONFLICT: (409, ErrorCode.INVITATION_NOT_PENDING),
    ErrorCode.INVITATION_EXPIRED: (409, ErrorCode.INVITATION_EXPIRED),
    ErrorCode.ACCESS_PERIOD_ENDED: (409, ErrorCode.ACCESS_PERIOD_ENDED),
}
_INBOX_MESSAGES = {
    ErrorCode.RESOURCE_NOT_FOUND: NOT_FOUND_MESSAGE,
    ErrorCode.INVITATION_NOT_PENDING: "이미 응답했거나 취소된 초대입니다.",
}


def _not_found() -> ApiError:
    return ApiError(404, ErrorCode.RESOURCE_NOT_FOUND, NOT_FOUND_MESSAGE)


def _verified_email(db: Session, worker_id: str) -> str | None:
    user = db.get(User, worker_id)
    if user is None or not user.email_verified:
        return None
    return normalize_email(user.google_email)


def _received_body(invitation: StoreInvitation, store: Store, now) -> dict:
    return {
        "id": invitation.id,
        "store": store_card(store),
        "status": invitation_status(invitation, now),
        "expiresAt": iso_or_none(invitation.expires_at),
        "accessExpiresAt": iso_or_none(invitation.access_expires_at),
        "createdAt": iso_or_none(invitation.created_at),
    }


def _own_invitation(db: Session, worker_id: str, invitation_id: str) -> tuple[StoreInvitation, Store]:
    email = _verified_email(db, worker_id)
    row = db.execute(
        select(StoreInvitation, Store).join(Store, Store.id == StoreInvitation.store_id)
        .where(StoreInvitation.id == normalize_uuid(invitation_id), email_is(StoreInvitation.invited_email, email))
    ).first() if email is not None else None
    if row is None:
        raise _not_found()
    return row[0], row[1]


@router.get("")
def list_received_invitations(
    worker: CurrentWorker, db: SessionDep, params: Pagination,
    filter: Literal["PENDING", "ALL"] = "PENDING",
) -> dict:
    now = utcnow()
    email = _verified_email(db, worker.user_id)
    items, total = [], 0
    if email is not None:
        conditions = [email_is(StoreInvitation.invited_email, email)]
        if filter == "PENDING":
            conditions.append(pending_invitation_clause(now))
        total = db.scalar(select(func.count()).select_from(StoreInvitation).where(*conditions))
        rows = db.execute(
            select(StoreInvitation, Store).join(Store, Store.id == StoreInvitation.store_id)
            .where(*conditions)
            .order_by(StoreInvitation.created_at.desc(), StoreInvitation.id.desc())
            .offset(params.offset).limit(params.limit)
        ).all()
        items = [_received_body(invitation, store, now) for invitation, store in rows]
    return {"items": items, "page": params.page, "size": params.size, "totalItems": total, "asOf": now.isoformat()}


@router.get("/{invitationId}")
def get_received_invitation(invitation_id: InvitationPath, worker: CurrentWorker, db: SessionDep) -> dict:
    invitation, store = _own_invitation(db, worker.user_id, invitation_id)
    return _received_body(invitation, store, utcnow())


class ResponseBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["ACCEPT", "DECLINE"]


def _inbox_error(error: ApiError) -> ApiError:
    mapped = _INBOX_ERRORS.get(error.code)
    if mapped is None:
        return error
    status, code = mapped
    return ApiError(status, code, _INBOX_MESSAGES.get(code, error.message))


@router.post("/{invitationId}/response")
def respond_to_received_invitation(
    invitation_id: InvitationPath, body: ResponseBody, worker: CsrfWorker, db: SessionDep,
    key: IdempotencyKey,
):
    invitation_id = normalize_uuid(invitation_id)

    def work() -> IdempotentResult:
        _own_invitation(db, worker.user_id, invitation_id)
        try:
            result = respond_to_invitation(
                db, worker.user_id, invitation_id, body.decision, commit=False, existing_regular="return",
            )
        except ApiError as error:
            raise _inbox_error(error) from None
        return IdempotentResult(200, result)

    def revalidate() -> None:
        _own_invitation(db, worker.user_id, invitation_id)  # still addressed to this account

    return run_idempotent(
        db=db, principal=worker, key=key, method="POST",
        path=f"/api/users/me/store-invitations/{invitation_id}/response", body=body, handler=work,
        revalidate=revalidate,
    )
