"""WORK_REMINDER: the day-before notice for a confirmed shift (docs/notification-design.md).

Policy (the send time is an operations setting per the spec): from `WORK_REMINDER_HOUR` o'clock
Asia/Seoul (default 18) until midnight, every active confirmed shift whose Seoul work date is
tomorrow gets one reminder. A shift confirmed later that evening is still reminded at the next
sweep; a shift withdrawn before the sweep is skipped; a shift on the same day or in the past is
never reminded (it is not "tomorrow" any more). If the service is down for the whole evening the
reminder is skipped rather than sent on the work day itself.

Exactly once: the event key is the shift ID, so `record_notification` keeps one row per shift
however many sweeps or processes see it. Rows already reminded are excluded in SQL so a sweep
does not rescan them, and candidates are locked `FOR UPDATE OF shift_assignments SKIP LOCKED` so
concurrent processes split the work and a withdrawal in progress is retried next sweep instead
of racing it.
"""
import os
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import String, exists, literal, select

from app.db import session_scope
from app.db.models import JOB_STATUSES, JobPosting, Notification, ShiftAssignment, Store
from app.notifications import (
    NotificationType,
    SensitiveTextError,
    WorkScheduleTarget,
    record_notification,
)

SEOUL = ZoneInfo("Asia/Seoul")
REMINDER_HOUR_ENV = "WORK_REMINDER_HOUR"
DEFAULT_REMINDER_HOUR = 18
BATCH_SIZE = 200
MAX_BATCHES = 10


class ReminderConfigurationError(ValueError):
    pass


def reminder_hour() -> int:
    """`WORK_REMINDER_HOUR`: an hour 0..23 in Asia/Seoul; unset or empty means 18."""
    raw = os.getenv(REMINDER_HOUR_ENV, "").strip()
    if not raw:
        return DEFAULT_REMINDER_HOUR
    if not raw.isascii() or not raw.isdigit() or not 0 <= int(raw) <= 23:
        raise ReminderConfigurationError(f"{REMINDER_HOUR_ENV} must be an hour from 0 to 23")
    return int(raw)


def due_work_date(now: datetime, hour: int) -> date | None:
    """The Seoul work date to remind about at `now`, or None before the reminder hour."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    local = now.astimezone(SEOUL)
    return local.date() + timedelta(days=1) if local.hour >= hour else None


def _when(job: JobPosting) -> str:
    end = f"{job.end_time:%H:%M}" + ("(익일)" if job.ends_next_day else "")
    return f"{job.work_date.month}월 {job.work_date.day}일 {job.start_time:%H:%M}–{end}"


def reminder_body(store: Store, job: JobPosting) -> str:
    return f"{store.name} {_when(job)}"


def reminder_body_without_store(job: JobPosting) -> str:
    return f"{_when(job)} 근무가 있어요"


def _already_reminded(dialect: str):
    key = literal(f"{NotificationType.WORK_REMINDER}:", String) + ShiftAssignment.id
    if dialect in ("mysql", "mariadb"):
        key = key.collate("utf8mb4_0900_as_cs")  # dedupe_key's collation; avoids an illegal mix
    return exists().where(
        Notification.recipient_user_id == ShiftAssignment.worker_id, Notification.dedupe_key == key,
    )


def sweep_work_reminders(now: datetime) -> int:
    """Record due reminders in bounded, separately committed batches; returns how many were added."""
    work_date = due_work_date(now, reminder_hour())
    if work_date is None:
        return 0
    recorded = 0
    for _ in range(MAX_BATCHES):
        with session_scope() as db:
            rows = db.execute(
                select(ShiftAssignment, JobPosting, Store)
                .join(JobPosting, JobPosting.id == ShiftAssignment.job_id)
                .join(Store, Store.id == JobPosting.store_id)
                .where(
                    # Every status, spelled out: it filters nothing, but lets MySQL drive the scan
                    # with the (status, work_date) index instead of reading every shift ever made.
                    ShiftAssignment.withdrawn_at.is_(None), JobPosting.status.in_(JOB_STATUSES),
                    JobPosting.work_date == work_date,
                    ~_already_reminded(db.get_bind().dialect.name),
                )
                .order_by(ShiftAssignment.id)
                .limit(BATCH_SIZE)
                .with_for_update(skip_locked=True, of=ShiftAssignment)
            ).all()
            for shift, job, store in rows:
                target = WorkScheduleTarget(event_id=shift.id, store_id=store.id, work_date=job.work_date)
                try:
                    body = reminder_body(store, job)
                    record_notification(
                        db, recipient_user_id=shift.worker_id, type=NotificationType.WORK_REMINDER,
                        target=target, event_key=shift.id, body=body, created_at=now,
                    )
                except SensitiveTextError:
                    # A store name that looks like a number/URL must not block the reminder.
                    record_notification(
                        db, recipient_user_id=shift.worker_id, type=NotificationType.WORK_REMINDER,
                        target=target, event_key=shift.id, body=reminder_body_without_store(job),
                        created_at=now,
                    )
            recorded += len(rows)
        if len(rows) < BATCH_SIZE:
            break
    return recorded

