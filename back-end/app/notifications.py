"""In-app notifications: the outbox other domains write inside their own transaction.

Contract (docs/notification-design.md, docs/erd/notification.md, openapi.yaml):

* A notification is stored by `record_notification` in the same transaction as the state change
  that causes it. The caller commits (or `run_idempotent` does); a rolled-back change leaves no
  notification behind, a committed one always has it.
* The row is the delivery. The MVP has only the in-app list (no external push), so nothing reads
  this table to send elsewhere and no delivery failure can undo the business change.
* "Event + recipient" is unique: the same domain event recorded twice (a retried job, a replayed
  transition) yields one row. Callers pass `event_key`, the identity of the event that caused the
  notification (see `EVENT_KEY_GUIDE`); the stored `dedupe_key` is "<TYPE>:<event_key>".
* The target is typed: each `NotificationType` allows only certain target classes and every
  target class carries exactly the IDs the API `NotificationTarget` requires. The stored target
  is a snapshot; the target's own API re-checks access when the user follows it, so an old
  notification never restores access (a cancelled invitation still shows its invitation ID).
* Title and body are user-facing text snapshots. They may not carry URLs, tokens, e-mail
  addresses or phone/registration numbers (`SensitiveTextError`).
"""

import re
import unicodedata
import uuid
from dataclasses import dataclass, fields
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from typing import Any, ClassVar

from sqlalchemy import select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.db import utcnow
from app.db.models import Notification


class NotificationType(StrEnum):
    NEW_APPLICATION = "NEW_APPLICATION"
    INVITATION_ACCEPTED = "INVITATION_ACCEPTED"
    STORE_APPROVED = "STORE_APPROVED"
    INVITATION_EXPIRED = "INVITATION_EXPIRED"
    WORK_REQUEST_RECEIVED = "WORK_REQUEST_RECEIVED"
    WORK_REQUEST_NO_RESPONSE = "WORK_REQUEST_NO_RESPONSE"
    WORK_CONFIRMED = "WORK_CONFIRMED"
    STORE_INVITED = "STORE_INVITED"
    WORK_REMINDER = "WORK_REMINDER"
    MANUAL_PUBLISHED = "MANUAL_PUBLISHED"
    WORK_REQUEST_WITHDRAWN = "WORK_REQUEST_WITHDRAWN"
    WORK_CONFIRMATION_WITHDRAWN = "WORK_CONFIRMATION_WITHDRAWN"


class TargetKind(StrEnum):
    JOB_APPLICATION = "JOB_APPLICATION"
    WORK_REQUEST = "WORK_REQUEST"
    STORE_INVITATION = "STORE_INVITATION"
    STORE = "STORE"
    MANUAL = "MANUAL"
    WORK_SCHEDULE = "WORK_SCHEDULE"


_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def _canonical_uuid(value: Any, name: str) -> str:
    if not isinstance(value, str) or not _UUID.match(value.lower()):
        raise ValueError(f"{name} must be a UUID string")
    return value.lower()


@dataclass(frozen=True)
class _Target:
    """Base of the typed targets. Field order is the API order; `target_id` is the first field."""

    kind: ClassVar[TargetKind]
    # Python attribute -> API property, in API order.
    _api_names: ClassVar[dict[str, str]]

    def __post_init__(self) -> None:
        for item in fields(self):
            value = getattr(self, item.name)
            if item.name == "work_date":
                # datetime is a date subclass; a timestamp is not a Seoul calendar date.
                if not isinstance(value, date) or isinstance(value, datetime):
                    raise ValueError("work_date must be a date (Asia/Seoul work start date)")
            else:
                object.__setattr__(self, item.name, _canonical_uuid(value, item.name))

    @property
    def target_id(self) -> str:
        return getattr(self, fields(self)[0].name)

    def to_api(self) -> dict[str, str]:
        body = {"type": str(self.kind)}
        for item in fields(self):
            value = getattr(self, item.name)
            body[self._api_names[item.name]] = value.isoformat() if isinstance(value, date) else value
        return body


@dataclass(frozen=True)
class JobApplicationTarget(_Target):
    application_id: str
    store_id: str
    job_id: str
    kind: ClassVar[TargetKind] = TargetKind.JOB_APPLICATION
    _api_names: ClassVar[dict[str, str]] = {
        "application_id": "applicationId", "store_id": "storeId", "job_id": "jobId",
    }


@dataclass(frozen=True)
class WorkRequestTarget(_Target):
    request_id: str
    store_id: str
    job_id: str
    kind: ClassVar[TargetKind] = TargetKind.WORK_REQUEST
    _api_names: ClassVar[dict[str, str]] = {
        "request_id": "requestId", "store_id": "storeId", "job_id": "jobId",
    }


@dataclass(frozen=True)
class StoreInvitationTarget(_Target):
    invitation_id: str
    kind: ClassVar[TargetKind] = TargetKind.STORE_INVITATION
    _api_names: ClassVar[dict[str, str]] = {"invitation_id": "invitationId"}


@dataclass(frozen=True)
class StoreTarget(_Target):
    store_id: str
    kind: ClassVar[TargetKind] = TargetKind.STORE
    _api_names: ClassVar[dict[str, str]] = {"store_id": "storeId"}


@dataclass(frozen=True)
class ManualTarget(_Target):
    store_id: str
    kind: ClassVar[TargetKind] = TargetKind.MANUAL
    _api_names: ClassVar[dict[str, str]] = {"store_id": "storeId"}


@dataclass(frozen=True)
class WorkScheduleTarget(_Target):
    event_id: str
    store_id: str
    work_date: date  # Asia/Seoul date the shift starts; the calendar opens that month
    kind: ClassVar[TargetKind] = TargetKind.WORK_SCHEDULE
    _api_names: ClassVar[dict[str, str]] = {
        "event_id": "eventId", "store_id": "storeId", "work_date": "workDate",
    }


NotificationTarget = (
    JobApplicationTarget | WorkRequestTarget | StoreInvitationTarget | StoreTarget | ManualTarget
    | WorkScheduleTarget
)
_TARGET_CLASSES: dict[TargetKind, type[_Target]] = {
    cls.kind: cls for cls in (
        JobApplicationTarget, WorkRequestTarget, StoreInvitationTarget, StoreTarget, ManualTarget,
        WorkScheduleTarget,
    )
}

# Which targets each type may open. Withdrawals open the ended WORK_REQUEST, never the cancelled
# schedule (openapi: 확정 철회 알림은 취소 일정으로 이동하지 않습니다).
ALLOWED_TARGETS: dict[NotificationType, tuple[type[_Target], ...]] = {
    NotificationType.NEW_APPLICATION: (JobApplicationTarget,),
    NotificationType.INVITATION_ACCEPTED: (StoreTarget,),
    NotificationType.STORE_APPROVED: (StoreTarget,),
    NotificationType.INVITATION_EXPIRED: (StoreTarget, StoreInvitationTarget),
    NotificationType.WORK_REQUEST_RECEIVED: (WorkRequestTarget,),
    NotificationType.WORK_REQUEST_NO_RESPONSE: (WorkRequestTarget,),
    NotificationType.WORK_CONFIRMED: (WorkScheduleTarget,),
    NotificationType.STORE_INVITED: (StoreInvitationTarget,),
    NotificationType.WORK_REMINDER: (WorkScheduleTarget,),
    NotificationType.MANUAL_PUBLISHED: (ManualTarget,),
    NotificationType.WORK_REQUEST_WITHDRAWN: (WorkRequestTarget,),
    NotificationType.WORK_CONFIRMATION_WITHDRAWN: (WorkRequestTarget,),
}

# Default card titles (Figma copy); pass `title=` to override.
DEFAULT_TITLES: dict[NotificationType, str] = {
    NotificationType.NEW_APPLICATION: "새 지원자가 있어요",
    NotificationType.INVITATION_ACCEPTED: "근무자가 초대를 수락했어요",
    NotificationType.STORE_APPROVED: "매장이 승인됐어요",
    NotificationType.INVITATION_EXPIRED: "초대가 만료됐어요",
    NotificationType.WORK_REQUEST_RECEIVED: "근무 요청이 도착했어요",
    NotificationType.WORK_REQUEST_NO_RESPONSE: "근무 요청에 응답이 없어요",
    NotificationType.WORK_CONFIRMED: "대타 근무가 확정됐어요",
    NotificationType.STORE_INVITED: "매장에서 초대가 왔어요",
    NotificationType.WORK_REMINDER: "내일 근무가 있어요",
    NotificationType.MANUAL_PUBLISHED: "매뉴얼이 업데이트됐어요",
    NotificationType.WORK_REQUEST_WITHDRAWN: "근무 요청이 철회됐어요",
    NotificationType.WORK_CONFIRMATION_WITHDRAWN: "근무 확정이 철회됐어요",
}

# What identifies "the same event" per type (documented for callers; see docs/notification-design.md).
EVENT_KEY_GUIDE: dict[NotificationType, str] = {
    NotificationType.NEW_APPLICATION: "applicationId",
    NotificationType.INVITATION_ACCEPTED: "invitationId",
    NotificationType.STORE_APPROVED: "storeId",
    NotificationType.INVITATION_EXPIRED: "invitationId (an expired invitation cannot be resent)",
    NotificationType.WORK_REQUEST_RECEIVED: "requestId",
    NotificationType.WORK_REQUEST_NO_RESPONSE: "requestId",
    NotificationType.WORK_CONFIRMED: "shift assignment ID (= calendar eventId)",
    NotificationType.STORE_INVITED: "invitationId + lastSentAt (a resend notifies again)",
    NotificationType.WORK_REMINDER: "shift assignment ID",
    NotificationType.MANUAL_PUBLISHED: "published manual version ID",
    NotificationType.WORK_REQUEST_WITHDRAWN: "requestId",
    NotificationType.WORK_CONFIRMATION_WITHDRAWN: "requestId",
}

TITLE_MAX_LENGTH = 100
BODY_MAX_LENGTH = 500
EVENT_KEY_MAX_LENGTH = 200
_EVENT_KEY = re.compile(r"^[A-Za-z0-9._:+-]+$")

# Text that must never reach a notification (spec: no external URL or token; minimal personal data).
# Checked on the NFKC form, so full-width digits and "＠" cannot slip past.
_SENSITIVE_PATTERNS = (
    re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://|\bwww\."),  # URLs
    # Bare domains ("evil.com/abc") of the common TLDs.
    re.compile(r"(?i)\b[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.(?:com|net|org|kr|io|me|ly|gl|link|app|xyz|info|shop|site|page)\b"),
    re.compile(r"[^\s@]+@[^\s@]+\.[^\s@]+"),  # e-mail addresses
    re.compile(r"\d{9,}"),  # phone, business registration and other long numbers
    # Formatted phone numbers: 010-1234-5678, (010)1234-5678, 010_1234_5678, +82-10-1234-5678.
    re.compile(r"(?<!\d)(?:\+\s*82[\s.\-()]*0?|\(?0)\d{1,2}\)?[\s.\-_)/]*\d{3,4}[\s.\-_/]*\d{4}(?!\d)"),
    re.compile(r"(?<!\d)\d{3}-\d{2}-\d{5}(?!\d)"),  # business registration number 123-45-67890
    re.compile(r"[A-Za-z0-9_-]{32,}"),  # tokens, hashes, UUIDs
)
_CONTROL = re.compile(r"[\x00-\x09\x0b-\x1f\x7f]")  # newline (\x0a) is allowed in the body


_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class SensitiveTextError(ValueError):
    """Title/body contains a URL, token, e-mail, phone/registration number or control character."""


def notification_event_key(*parts: str | int | date | datetime) -> str:
    """Join the parts that identify an event: `notification_event_key(invitation.id, invitation.expires_at)`.

    Datetimes become UTC microseconds since the epoch so the key does not depend on formatting.
    """
    if not parts:
        raise ValueError("an event key needs at least one part")
    rendered = []
    for part in parts:
        if isinstance(part, datetime):
            if part.tzinfo is None:
                raise ValueError("event key datetimes must be timezone-aware")
            delta = part.astimezone(UTC) - _EPOCH
            rendered.append(str(delta // timedelta(microseconds=1)))
        elif isinstance(part, date):
            rendered.append(part.isoformat())
        elif isinstance(part, bool) or not isinstance(part, str | int):
            raise TypeError("event key parts must be str, int, date or datetime")
        else:
            rendered.append(str(part))
    return ":".join(rendered)



def _text(value: Any, name: str, max_length: int, *, multiline: bool) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    value = value.strip()
    if not value or len(value) > max_length:
        raise ValueError(f"{name} must have 1..{max_length} characters")
    if _CONTROL.search(value) or (not multiline and "\n" in value):
        raise SensitiveTextError(f"{name} contains control characters")
    folded = unicodedata.normalize("NFKC", value)
    if any(pattern.search(folded) for pattern in _SENSITIVE_PATTERNS):
        raise SensitiveTextError(f"{name} looks like it contains a URL, token or personal data")
    return value


def body_or(preferred: str, fallback: str) -> str:
    """`preferred` when it is a valid body, else `fallback` (which must be valid itself).

    For text built from user-entered names (store name, posting title): a name that looks like a
    phone number must not make the business transition that notifies fail.
    """
    try:
        return _text(preferred, "body", BODY_MAX_LENGTH, multiline=True)
    except (TypeError, ValueError):
        return _text(fallback, "body", BODY_MAX_LENGTH, multiline=True)


def target_from_api(data: Any) -> NotificationTarget:
    """Rebuild a typed target from its API form (also validates stored snapshots)."""
    if not isinstance(data, dict):
        raise TypeError("target must be an object")
    try:
        cls = _TARGET_CLASSES[TargetKind(data.get("type"))]
    except ValueError:
        raise ValueError("unknown target type") from None
    names = {api: attr for attr, api in cls._api_names.items()}
    if set(data) != {"type", *names}:
        raise ValueError("target fields do not match its type")
    values = {}
    for api, attr in names.items():
        value = data[api]
        if attr == "work_date":
            if not isinstance(value, str):
                raise ValueError("workDate must be YYYY-MM-DD")
            value = date.fromisoformat(value)
        values[attr] = value
    return cls(**values)


@dataclass(frozen=True)
class _Prepared:
    type: NotificationType
    target: NotificationTarget
    title: str
    body: str
    dedupe_key: str


def _prepare(
    type: NotificationType | str, target: NotificationTarget, event_key: str, title: str | None, body: str,
) -> _Prepared:
    try:
        kind = NotificationType(type)
    except ValueError:
        raise ValueError(f"unknown notification type {type!r}") from None
    if not isinstance(target, ALLOWED_TARGETS[kind]):
        allowed = ", ".join(cls.__name__ for cls in ALLOWED_TARGETS[kind])
        raise TypeError(f"{kind} needs a target of type {allowed}")
    if (
        not isinstance(event_key, str) or not event_key or len(event_key) > EVENT_KEY_MAX_LENGTH
        or not _EVENT_KEY.match(event_key)
    ):
        raise ValueError("event_key must be 1..200 characters of [A-Za-z0-9._:+-]")
    return _Prepared(
        type=kind,
        target=target,
        title=_text(DEFAULT_TITLES[kind] if title is None else title, "title", TITLE_MAX_LENGTH, multiline=False),
        body=_text(body, "body", BODY_MAX_LENGTH, multiline=True),
        dedupe_key=f"{kind}:{event_key}",
    )


def _find(db: Session, recipient_user_id: str, dedupe_key: str) -> Notification:
    # A locking read sees rows committed after this transaction's snapshot (MySQL REPEATABLE READ).
    return db.execute(
        select(Notification)
        .where(Notification.recipient_user_id == recipient_user_id, Notification.dedupe_key == dedupe_key)
        .with_for_update()
    ).scalar_one()


def record_notification(
    db: Session,
    *,
    recipient_user_id: str,
    type: NotificationType | str,
    target: NotificationTarget,
    event_key: str,
    body: str,
    title: str | None = None,
    created_at: datetime | None = None,
) -> Notification:
    """Add one notification to the caller's transaction and return it. Never commits.

    Call it next to the state change that causes it, before the caller's commit:

        record_notification(
            db, recipient_user_id=worker.id, type=NotificationType.WORK_CONFIRMED,
            target=WorkScheduleTarget(event_id=shift.id, store_id=store.id, work_date=seoul_date),
            event_key=shift.id, body="컴포즈커피 광운대점 10월 10일 09:00–14:00",
        )

    * `event_key` identifies the causing event (`EVENT_KEY_GUIDE`, `notification_event_key`). If this
      recipient already has a notification for the same type and event key, nothing is added and
      the existing row is returned; reusing the key for a different target is a programming error
      (ValueError).
    * The insert is an upsert that does nothing on a duplicate (no SAVEPOINT, no error), so a
      duplicate never breaks the caller's transaction. A concurrent duplicate waits for the other
      transaction on the unique index and then returns its row.
    * Raises TypeError for a target class the type does not allow, ValueError for an unknown type
      or malformed IDs/keys/text (`SensitiveTextError` for URLs, tokens or personal data), and
      IntegrityError if the recipient does not exist. All of these are programming errors.
    """
    prepared = _prepare(type, target, event_key, title, body)
    recipient_user_id = _canonical_uuid(recipient_user_id, "recipient_user_id")
    if created_at is not None and created_at.tzinfo is None:
        raise ValueError("created_at must be timezone-aware")
    values = {
        "id": str(uuid.uuid4()),
        "recipient_user_id": recipient_user_id,
        "event_type": str(prepared.type),
        "title": prepared.title,
        "body": prepared.body,
        "target_kind": str(prepared.target.kind),
        "target_id": prepared.target.target_id,
        "target_context": prepared.target.to_api(),
        "dedupe_key": prepared.dedupe_key,
        "created_at": created_at or utcnow(),
    }
    db.flush()  # the caller's pending rows (e.g. a new store this notification refers to) go first
    db.execute(_insert_unless_duplicate(db, values))
    row = _find(db, recipient_user_id, prepared.dedupe_key)
    if row.target_kind != values["target_kind"] or row.target_context != values["target_context"]:
        raise ValueError(f"event key {prepared.dedupe_key!r} was already used for another target")
    return row


def _insert_unless_duplicate(db: Session, values: dict[str, Any]):
    # Not INSERT IGNORE: that would also turn CHECK and FK violations into warnings on MySQL.
    if db.get_bind().dialect.name in ("mysql", "mariadb"):
        statement = mysql_insert(Notification).values(**values)
        return statement.on_duplicate_key_update(id=Notification.id)  # keep the existing row as is
    statement = sqlite_insert(Notification).values(**values)
    return statement.on_conflict_do_nothing(index_elements=["recipient_user_id", "dedupe_key"])


def notification_body(row: Notification) -> dict[str, Any]:
    """The API `Notification` object. Never exposes the recipient or the dedupe key."""
    return {
        "id": row.id,
        "type": row.event_type,
        "title": row.title,
        "body": row.body,
        "target": target_from_api(row.target_context).to_api(),
        "createdAt": row.created_at.isoformat(),
        "readAt": row.read_at.isoformat() if row.read_at else None,
    }
