"""Worker invitations, owner side (docs/store-invitation-design.md, docs/erd/access.md).

Owners invite an e-mail address to an APPROVED store, list, resend and cancel invitations. The
invitee side (preview, accept, decline) is app.invitation_responses; it shares the state rules,
helpers and lock order defined here.

State is not stored as a column. It is derived from the outcome timestamps and the two
deadlines (`invitation_status`): ACCEPTED / DECLINED / CANCELLED once recorded, otherwise
EXPIRED as soon as `now` reaches the 7 day link end or the optional access end (both
exclusive), otherwise PENDING.

Concurrency. Every state change locks the store row and then the invitation row (always in
that order, the same order as access revocation), and every transition is also a conditional
UPDATE on "no outcome yet", so one change wins even without row locks (SQLite).

E-mail addresses are compared and stored trimmed and lower-cased; dots and plus aliases are
kept as written (`normalize_email`). Raw tokens are never stored, logged or returned.
"""

import hashlib
import re
import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Path
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import case, exists, func, select, update
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from app import notification_events
from app.auth import CurrentOwner
from app.csrf import CsrfOwner
from app.db import SessionDep, iso_utc, utcnow
from app.db.models import StoreAccessGrant, StoreInvitation, User
from app.email_match import email_is
from app.errors import ApiError, ErrorCode
from app.idempotency import REPLAY_HEADER, IdempotencyKey, IdempotentResult, run_idempotent
from app.invitation_mail import (
    DeliveryUnavailable,
    deliver_queued_mail,
    discard_queued_mail,
    enqueue_invitation_mail,
)
from app.pagination import Pagination, page_response
from app.store_access import (
    UUID_PATTERN,
    StoreIdPath,
    load_owned_store,
    normalize_uuid,
    pending_invitation_clause,
    valid_grant_clause,
)

router = APIRouter()

LINK_LIFETIME = timedelta(days=7)
PENDING = "PENDING"
ACCEPTED = "ACCEPTED"
DECLINED = "DECLINED"
CANCELLED = "CANCELLED"
EXPIRED = "EXPIRED"

MESSAGES = {
    ErrorCode.INVITATION_NOT_FOUND: "초대를 찾을 수 없습니다.",
    ErrorCode.INVITATION_ALREADY_PENDING: "이미 수락 대기 중인 초대가 있습니다.",
    ErrorCode.INVITATION_STATE_CONFLICT: "현재 초대 상태에서는 이 작업을 할 수 없습니다.",
    ErrorCode.INVITATION_EXPIRED: "초대 유효 기간이 지났습니다.",
    ErrorCode.ACCESS_PERIOD_ENDED: "지정된 자료 접근 기간이 종료되었습니다.",
    ErrorCode.INVITATION_EMAIL_MISMATCH: "초대 받은 Google 계정으로 로그인해 주세요.",
    ErrorCode.WORKER_ALREADY_HAS_ACCESS: "이미 이 매장의 자료 접근 권한이 있습니다.",
    ErrorCode.STORE_APPROVAL_REQUIRED: "매장 운영 승인 후 이용할 수 있습니다.",
}
ACCEPT_CONFLICT_MESSAGE = "현재 초대 상태에서는 수락할 수 없습니다."
CREATE_DELIVERY_MESSAGE = "초대 메일 발송을 요청하지 못했습니다. 잠시 후 다시 시도해 주세요."
RESEND_DELIVERY_MESSAGE = "재전송을 요청하지 못했습니다. 잠시 후 다시 시도해 주세요."


def error(status: int, code: ErrorCode, message: str | None = None) -> ApiError:
    return ApiError(status, code, message or MESSAGES.get(code))


# --- e-mail and tokens ----------------------------------------------------------------------

MAX_EMAIL_LENGTH = 254
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
# OpenAPI `format: email` is an ASCII (RFC 5321) mailbox; `idn-email` is not offered.
_ASCII_SPACE = " \t\n\r\f\v"
_PRINTABLE_ASCII = re.compile(r"^[\x21-\x7e]*$")


def normalize_email(value: str) -> str:
    """Trim and lower-case. Dots, plus tags and other aliases are deliberately kept."""
    return value.strip().lower()



def generate_invitation_token() -> str:
    return secrets.token_urlsafe(32)  # 256 bits, 43 base64url characters


def hash_invitation_token(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


# --- state ----------------------------------------------------------------------------------

def invitation_status(invitation: StoreInvitation, now: datetime) -> str:
    if invitation.accepted_at is not None:
        return ACCEPTED
    if invitation.declined_at is not None:
        return DECLINED
    if invitation.canceled_at is not None:
        return CANCELLED
    if expiry_code(invitation, now) is not None:
        return EXPIRED
    return PENDING


def expiry_code(invitation: StoreInvitation, now: datetime) -> ErrorCode | None:
    """Which deadline `now` has reached first, if any (both ends are exclusive)."""
    ended = [
        (deadline, code) for deadline, code in (
            (invitation.access_expires_at, ErrorCode.ACCESS_PERIOD_ENDED),
            (invitation.expires_at, ErrorCode.INVITATION_EXPIRED),
        ) if deadline is not None and deadline <= now
    ]
    return min(ended, key=lambda item: item[0])[1] if ended else None


def iso_or_none(value: datetime | None) -> str | None:
    return iso_utc(value)


def invitation_body(invitation: StoreInvitation, now: datetime, accepted_by_name: str | None) -> dict:
    status = invitation_status(invitation, now)
    return {
        "id": invitation.id,
        "storeId": invitation.store_id,
        "email": invitation.invited_email,
        "status": status,
        "createdAt": iso_or_none(invitation.created_at),
        "lastSentAt": iso_or_none(invitation.last_sent_at),
        "expiresAt": iso_or_none(invitation.expires_at),
        "accessExpiresAt": iso_or_none(invitation.access_expires_at),
        "acceptedAt": iso_or_none(invitation.accepted_at),
        "acceptedBy": (
            {"workerId": invitation.accepted_by_worker_id, "name": accepted_by_name}
            if status == ACCEPTED else None
        ),
        "declinedAt": iso_or_none(invitation.declined_at),
        "canceledAt": iso_or_none(invitation.canceled_at),
    }


def _accepted_by_name(db: Session, invitation: StoreInvitation) -> str | None:
    if invitation.accepted_by_worker_id is None:
        return None
    return db.scalar(select(User.name).where(User.id == invitation.accepted_by_worker_id))


def _delivery_response(response: Response) -> Response:
    """Deliver queued mail after the response, only for the request that actually queued it."""
    if REPLAY_HEADER not in response.headers:
        # Claims rows like the periodic job does, so the two never send the same mail twice.
        response.background = BackgroundTask(deliver_queued_mail)
    return response


# --- owner: create ----------------------------------------------------------------------------

class InvitationCreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: Annotated[str, Field(strict=True)]
    accessExpiresAt: datetime | None = None

    @field_validator("email")
    @classmethod
    def _email(cls, value: str) -> str:
        # Only ASCII spaces are trimmed. Anything else outside printable ASCII (NBSP, zero-width
        # characters, full-width letters, accents, look-alike Cyrillic) is refused, not folded:
        # MySQL would treat some of them as equal to plain letters and the invitee would differ
        # from what the owner typed.
        value = value.strip(_ASCII_SPACE)
        if not _PRINTABLE_ASCII.match(value):
            raise ValueError("invalid email")
        value = value.lower()
        if len(value) > MAX_EMAIL_LENGTH or not _EMAIL.match(value):
            raise ValueError("invalid email")
        return value

    @field_validator("accessExpiresAt", mode="before")
    @classmethod
    def _rfc3339(cls, value):
        # RFC 3339 date-time with an explicit offset; dates, numbers and naive times are refused.
        if value is None:
            return None
        if not isinstance(value, str) or not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}(\.\d{1,6})?([Zz]|[+-]\d{2}:\d{2})", value,
        ):
            raise ValueError("RFC 3339 date-time with offset required")
        return value

    @field_validator("accessExpiresAt")
    @classmethod
    def _utc(cls, value: datetime | None) -> datetime | None:
        return None if value is None else value.astimezone(UTC)


def _field_error(field: str, code: str, message: str) -> ApiError:
    return ApiError(422, ErrorCode.VALIDATION_ERROR, field_errors=[{"field": field, "code": code, "message": message}])


def regular_grant_clause():
    """REGULAR grants only: a TEMPORARY (substitute shift) grant coexists independently with a
    regular one (openapi work request response), so it never blocks an invitation."""
    return StoreAccessGrant.invitation_id.is_not(None)


def _has_valid_access(db: Session, store_id: str, email: str, now: datetime) -> bool:
    return db.scalar(select(exists().where(
        StoreAccessGrant.store_id == store_id,
        StoreAccessGrant.worker_id == User.id,
        email_is(func.lower(func.trim(User.google_email)), email),
        regular_grant_clause(),
        valid_grant_clause(now),
    ))) or False


@router.post("/api/stores/{storeId}/invitations", status_code=201)
def create_invitation(
    store_id: StoreIdPath, body: InvitationCreateBody, owner: CsrfOwner, db: SessionDep,
    key: IdempotencyKey,
):
    def work() -> IdempotentResult:
        store = load_owned_store(db, owner.user_id, store_id, lock=True)
        now = utcnow()
        owner_email = db.scalar(select(User.google_email).where(User.id == owner.user_id))
        if owner_email is not None and normalize_email(owner_email) == body.email:
            raise _field_error("email", "INVALID_FORMAT", "본인 이메일은 초대할 수 없습니다.")
        if body.accessExpiresAt is not None and body.accessExpiresAt <= now:
            raise _field_error("accessExpiresAt", "OUT_OF_RANGE", "접근 종료 시각은 현재보다 미래여야 합니다.")
        if db.scalar(select(exists().where(
            StoreInvitation.store_id == store.id, email_is(StoreInvitation.invited_email, body.email),
            pending_invitation_clause(now),
        ))):
            raise error(409, ErrorCode.INVITATION_ALREADY_PENDING)
        if _has_valid_access(db, store.id, body.email, now):
            raise error(409, ErrorCode.WORKER_ALREADY_HAS_ACCESS)
        token = generate_invitation_token()
        invitation = StoreInvitation(
            store_id=store.id, inviter_owner_id=owner.user_id, invited_email=body.email,
            token_hash=hash_invitation_token(token), created_at=now, last_sent_at=now,
            expires_at=now + LINK_LIFETIME, access_expires_at=body.accessExpiresAt,
        )
        db.add(invitation)
        db.flush()
        try:
            enqueue_invitation_mail(db, invitation, store.name, token)
        except DeliveryUnavailable:
            raise error(503, ErrorCode.DELIVERY_UNAVAILABLE, CREATE_DELIVERY_MESSAGE) from None
        # notification: STORE_INVITED (to a registered WORKER whose verified e-mail matches;
        # target STORE_INVITATION=invitation.id, never the token)
        notification_events.store_invited(db, invitation, store)
        return IdempotentResult(201, {"invitation": invitation_body(invitation, now, None), "deliveryStatus": "QUEUED"})

    response = run_idempotent(
        db=db, principal=owner, key=key, method="POST", path=f"/api/stores/{normalize_uuid(store_id)}/invitations",
        body=body, handler=work, revalidate=lambda: load_owned_store(db, owner.user_id, store_id),
    )
    return _delivery_response(response)


# --- owner: list ------------------------------------------------------------------------------

@router.get("/api/stores/{storeId}/invitations")
def list_invitations(
    store_id: StoreIdPath, owner: CurrentOwner, db: SessionDep, params: Pagination,
    view: Literal["ACTIVE", "PAST"] = "ACTIVE",
) -> dict:
    store = load_owned_store(db, owner.user_id, store_id)
    now = utcnow()
    active = pending_invitation_clause(now)
    total_all, active_count = db.execute(
        select(func.count(), func.coalesce(func.sum(case((active, 1), else_=0)), 0))
        .select_from(StoreInvitation).where(StoreInvitation.store_id == store.id)
    ).one()
    active_count = int(active_count)
    selected = active if view == "ACTIVE" else ~active
    rows = db.execute(
        select(StoreInvitation, User.name)
        .outerjoin(User, User.id == StoreInvitation.accepted_by_worker_id)
        .where(StoreInvitation.store_id == store.id, selected)
        .order_by(StoreInvitation.created_at.desc(), StoreInvitation.id.desc())
        .offset(params.offset).limit(params.limit)
    ).all()
    total = active_count if view == "ACTIVE" else total_all - active_count
    body = page_response([invitation_body(inv, now, name) for inv, name in rows], total, params)
    body.update({"asOf": iso_or_none(now), "activeCount": active_count, "pastCount": total_all - active_count})
    return body


# --- owner: resend and cancel ----------------------------------------------------------------

InvitationIdPath = Annotated[str, Path(alias="invitationId", pattern=UUID_PATTERN)]


def _lock_store_invitation(db: Session, owner_id: str, store_id: str, invitation_id: str):
    store = load_owned_store(db, owner_id, store_id, lock=True)
    invitation = db.execute(
        select(StoreInvitation)
        .where(StoreInvitation.id == normalize_uuid(invitation_id), StoreInvitation.store_id == store.id)
        .with_for_update()
    ).scalar_one_or_none()
    if invitation is None:
        raise error(404, ErrorCode.INVITATION_NOT_FOUND)
    return store, invitation


def no_outcome(invitation_id: str):
    return (
        StoreInvitation.id == invitation_id,
        StoreInvitation.accepted_at.is_(None),
        StoreInvitation.declined_at.is_(None),
        StoreInvitation.canceled_at.is_(None),
    )


def refuse_unless_pending(invitation: StoreInvitation, now: datetime) -> None:
    status = invitation_status(invitation, now)
    if status in (ACCEPTED, DECLINED, CANCELLED):
        raise error(409, ErrorCode.INVITATION_STATE_CONFLICT)
    if status == EXPIRED:
        raise error(410, expiry_code(invitation, now))


@router.post("/api/stores/{storeId}/invitations/{invitationId}/resend")
def resend_invitation(
    store_id: StoreIdPath, invitation_id: InvitationIdPath, owner: CsrfOwner, db: SessionDep,
    key: IdempotencyKey,
):
    def work() -> IdempotentResult:
        store, invitation = _lock_store_invitation(db, owner.user_id, store_id, invitation_id)
        now = utcnow()
        refuse_unless_pending(invitation, now)
        token = generate_invitation_token()
        changed = db.execute(
            update(StoreInvitation).where(*no_outcome(invitation.id), StoreInvitation.expires_at > now)
            .values(token_hash=hash_invitation_token(token), last_sent_at=now)
            .execution_options(synchronize_session=False)
        ).rowcount
        if changed != 1:
            raise error(409, ErrorCode.INVITATION_STATE_CONFLICT)
        db.refresh(invitation)
        discard_queued_mail(db, invitation.id, now=now)
        try:
            enqueue_invitation_mail(db, invitation, store.name, token)
        except DeliveryUnavailable:
            raise error(503, ErrorCode.DELIVERY_UNAVAILABLE, RESEND_DELIVERY_MESSAGE) from None
        notification_events.store_invited(db, invitation, store)  # a resend notifies again
        return IdempotentResult(200, {"invitation": invitation_body(invitation, now, None), "deliveryStatus": "QUEUED"})

    path = f"/api/stores/{normalize_uuid(store_id)}/invitations/{normalize_uuid(invitation_id)}/resend"
    response = run_idempotent(
        db=db, principal=owner, key=key, method="POST", path=path, body=None, handler=work,
        revalidate=lambda: load_owned_store(db, owner.user_id, store_id),
    )
    return _delivery_response(response)


@router.post("/api/stores/{storeId}/invitations/{invitationId}/cancel")
def cancel_invitation(
    store_id: StoreIdPath, invitation_id: InvitationIdPath, owner: CsrfOwner, db: SessionDep,
) -> dict:
    db.commit()  # end the authentication read snapshot; the store lock comes first
    _, invitation = _lock_store_invitation(db, owner.user_id, store_id, invitation_id)
    now = utcnow()
    if invitation.canceled_at is None:
        refuse_unless_pending(invitation, now)
        changed = db.execute(
            update(StoreInvitation).where(*no_outcome(invitation.id))
            .values(canceled_at=now).execution_options(synchronize_session=False)
        ).rowcount
        if changed != 1:
            raise error(409, ErrorCode.INVITATION_STATE_CONFLICT)
        discard_queued_mail(db, invitation.id, now=now)
        db.commit()
        db.refresh(invitation)
    return invitation_body(invitation, now, _accepted_by_name(db, invitation))
