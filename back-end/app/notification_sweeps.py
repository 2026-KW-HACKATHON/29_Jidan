"""Time-based notifications: the sweeps that record notifications nobody's request triggers.

A sweep is `def sweep(now: datetime) -> int`: it records every notification due at `now` with
`app.notifications.record_notification` and returns how many it added. Contract:

* Idempotent. Use the event key of the notification type (docs/notification-design.md) so a
  repeated or concurrent sweep adds nothing; excluding already-notified rows in SQL is an
  optimization, not the guarantee.
* Bounded. Work in batches (`LIMIT`) with a commit per batch (`session_scope()`), and stop after a
  fixed number of batches; the rest is picked up next time.
* Multi-process safe and non-blocking. Lock candidate rows `FOR UPDATE OF <table> SKIP LOCKED` so
  processes split the work and a domain transaction holding the row is retried later, never
  waited on.
* Deterministic. Use only the given `now`, never the wall clock. `created_at` is `now`, or the
  stored instant the event happened at when it has one (a request's or invitation's deadline):
  the same value whichever sweep or write records it first.
* Quiet. Never log personal data; failures are logged by sweep name only.

To add one (e.g. WORK_REQUEST_NO_RESPONSE from the work-request owner, INVITATION_EXPIRED from
the invitation owner), write the function in your domain module and append it to `SWEEPS`.
"""
import logging
from collections.abc import Callable
from datetime import datetime

from app.db import utcnow
from app.invitation_expiry import sweep_expired_invitations
from app.jobs.state import expire_due_requests
from app.work_reminders import sweep_work_reminders

logger = logging.getLogger("jidan.errors")

NotificationSweep = Callable[[datetime], int]

# (name, sweep). Names appear in logs and must not contain data.
SWEEPS: list[tuple[str, NotificationSweep]] = [
    ("work-reminder", sweep_work_reminders),
    # The jobs domain owns expiry: expire_due_requests materializes due requests through
    # expire_request, which records WORK_REQUEST_NO_RESPONSE in the same transaction.
    ("work-request-expiry", lambda now: expire_due_requests(now)),
    ("invitation-expiry", sweep_expired_invitations),
]
INTERVAL_SECONDS = 60


def run_notification_sweeps(now: datetime | None = None) -> dict[str, int | None]:
    """Run every sweep once at the same `now`. One failing sweep does not stop the others.

    Returns {name: notifications added, or None when the sweep failed}.
    """
    now = now or utcnow()
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    results: dict[str, int | None] = {}
    for name, sweep in SWEEPS:
        try:
            results[name] = sweep(now)
        except Exception:  # noqa: BLE001 - other sweeps still run; retried at the next interval
            logger.error("Notification sweep %s failed; retry at next interval", name)
            results[name] = None
    return results
