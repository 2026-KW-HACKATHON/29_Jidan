"""The member's own notifications: list with unread count, and marking one card as read."""
import re
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select, update

from app.auth import CurrentMember
from app.csrf import CsrfMember
from app.db import SessionDep, utcnow
from app.db.models import Notification
from app.errors import ApiError, ErrorCode
from app.idempotency import IdempotencyKey, IdempotentResult, run_idempotent
from app.notifications import notification_body
from app.pagination import Pagination

router = APIRouter(prefix="/api/users/me/notifications")

READ_FILTERS = ("ALL", "UNREAD")


def read_filter(request: Request) -> str:
    """`?read=ALL|UNREAD` (default ALL); anything else, or the parameter twice, is 422."""
    values = request.query_params.getlist("read")
    if not values:
        return "ALL"
    if len(values) > 1 or values[0] not in READ_FILTERS:
        raise ApiError(422, ErrorCode.VALIDATION_ERROR, field_errors=[
            {"field": "read", "code": "INVALID_FORMAT", "message": "ALL 또는 UNREAD로 입력해 주세요."},
        ])
    return values[0]


ReadFilter = Annotated[str, Depends(read_filter)]


@router.get("")
def list_my_notifications(member: CurrentMember, db: SessionDep, params: Pagination, read: ReadFilter) -> dict:
    # Every query uses one transaction snapshot and the same `asOf` bound, so items, totalItems and
    # unreadCount describe the same moment. Rows stamped later than asOf appear on the next call.
    as_of = utcnow()
    mine = (Notification.recipient_user_id == member.user_id, Notification.created_at <= as_of)
    unread = Notification.read_at.is_(None)
    listed = (*mine, unread) if read == "UNREAD" else mine
    unread_count = db.scalar(select(func.count()).select_from(Notification).where(*mine, unread))
    total = unread_count if read == "UNREAD" else db.scalar(
        select(func.count()).select_from(Notification).where(*listed),
    )
    rows = db.scalars(
        select(Notification).where(*listed)
        .order_by(Notification.created_at.desc(), Notification.id.desc())  # id breaks equal timestamps
        .offset(params.offset).limit(params.limit)
    ).all()
    return {
        "items": [notification_body(row) for row in rows],
        "page": params.page,
        "size": params.size,
        "totalItems": total,
        "asOf": as_of.isoformat(),
        "unreadCount": unread_count,
    }


_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


class NotificationRead(BaseModel):
    """The empty `NotificationRead` body; unknown properties are 422."""

    model_config = ConfigDict(extra="forbid")


def notification_id(notificationId: str) -> str:
    """The path UUID in canonical lower case (stored IDs are lower case)."""
    if not _UUID.match(notificationId):
        raise ApiError(422, ErrorCode.VALIDATION_ERROR, field_errors=[
            {"field": "notificationId", "code": "INVALID_FORMAT", "message": "UUID 형식이어야 합니다."},
        ])
    return notificationId.lower()


def _own(db, member, notification_id: str, *, lock: bool) -> Notification:
    # Someone else's notification and a missing one are the same 404.
    query = select(Notification).where(
        Notification.id == notification_id, Notification.recipient_user_id == member.user_id,
    )
    row = db.scalar(query.with_for_update() if lock else query)
    if row is None:
        raise ApiError(404, ErrorCode.RESOURCE_NOT_FOUND)
    return row


@router.post("/{notificationId}/read")
def mark_notification_read(
    member: CsrfMember, db: SessionDep, key: IdempotencyKey, body: NotificationRead,
    notification_uuid: Annotated[str, Depends(notification_id)],
):
    def work() -> IdempotentResult:
        # Concurrent reads with different keys: the row lock (MySQL) and the `read_at IS NULL`
        # condition (every database) let only the first set readAt; later ones keep it.
        _own(db, member, notification_uuid, lock=True)
        db.execute(
            update(Notification)
            .where(Notification.id == notification_uuid, Notification.read_at.is_(None))
            .values(read_at=utcnow())
        )
        # Already locked by us; a locking read also never depends on when the snapshot began.
        row = db.scalar(
            select(Notification).where(Notification.id == notification_uuid)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return IdempotentResult(200, notification_body(row))

    return run_idempotent(
        db=db, principal=member, key=key, method="POST", path=f"{router.prefix}/{notification_uuid}/read",
        body=body, handler=work, revalidate=lambda: _own(db, member, notification_uuid, lock=False),
    )
