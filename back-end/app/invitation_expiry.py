"""INVITATION_EXPIRED sweep: tell the owner when an unanswered invitation ran out.

Invitation expiry is not a stored transition (app.invitations derives EXPIRED from the
deadlines), so this sweep only records the notification. An invitation is due when it has no
outcome and its effective deadline -- the earlier of the link expiry and the access end, as
`expiry_code` decides -- is at or before `now`.

An invitation expires at most once: resend, cancel and accept all refuse an expired one, so its
deadline cannot move after it passed. The event key is therefore the invitation ID, and already
announced invitations are excluded in SQL (a Python-side filter after LIMIT would starve rows
behind a full batch of announced ones).

Only deadlines within `LOOKBACK` are swept: after a long outage a day-old expiry is not news,
and the bound keeps each run's scan small. Candidates are locked `FOR UPDATE OF
store_invitations SKIP LOCKED`, so concurrent processes split the work.
"""
from datetime import datetime, timedelta

from sqlalchemy import String, and_, case, exists, literal, select

from app import notification_events
from app.db import session_scope
from app.db.models import Notification, Store, StoreInvitation
from app.notifications import NotificationType

LOOKBACK = timedelta(days=1)
BATCH_SIZE = 200
MAX_BATCHES = 10

# The earlier of the two deadlines, as app.invitations.expiry_code decides.
EFFECTIVE_DEADLINE = case(
    (and_(StoreInvitation.access_expires_at.is_not(None),
          StoreInvitation.access_expires_at < StoreInvitation.expires_at), StoreInvitation.access_expires_at),
    else_=StoreInvitation.expires_at,
)


def _already_announced(dialect: str):
    key = literal(f"{NotificationType.INVITATION_EXPIRED}:", String) + StoreInvitation.id
    if dialect in ("mysql", "mariadb"):
        key = key.collate("utf8mb4_0900_as_cs")  # dedupe_key's collation; avoids an illegal mix
    return exists().where(Notification.recipient_user_id == Store.owner_id, Notification.dedupe_key == key)


def sweep_expired_invitations(now: datetime) -> int:
    """Record due INVITATION_EXPIRED notifications; returns how many invitations were handled."""
    since = now - LOOKBACK
    handled = 0
    for _ in range(MAX_BATCHES):
        with session_scope() as db:
            rows = db.execute(
                select(StoreInvitation, Store, EFFECTIVE_DEADLINE)
                .join(Store, Store.id == StoreInvitation.store_id)
                .where(
                    StoreInvitation.accepted_at.is_(None), StoreInvitation.declined_at.is_(None),
                    StoreInvitation.canceled_at.is_(None), EFFECTIVE_DEADLINE.between(since, now),
                    ~_already_announced(db.get_bind().dialect.name),
                )
                .order_by(StoreInvitation.id)
                .limit(BATCH_SIZE)
                .with_for_update(skip_locked=True, of=StoreInvitation)
            ).all()
            for invitation, store, _deadline in rows:
                notification_events.invitation_expired(db, invitation, store, effective_deadline(invitation))
            handled += len(rows)
        if len(rows) < BATCH_SIZE:
            break
    return handled


def effective_deadline(invitation: StoreInvitation) -> datetime:
    if invitation.access_expires_at is not None and invitation.access_expires_at < invitation.expires_at:
        return invitation.access_expires_at
    return invitation.expires_at
