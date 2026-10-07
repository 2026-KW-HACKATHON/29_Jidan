"""Shared pieces of the jobs domain: clock, Seoul shift times, store checks and serializers.

Work dates and wall-clock times are Asia/Seoul; every stored instant is UTC. `now()` is the
single clock of the domain so tests can pin exact boundaries (expiry, shift start).
"""
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.db import iso_utc, utcnow
from app.db.models import JobApplication, JobPosting, Store
from app.errors import ErrorCode
from app.store_access import load_owned_store, store_card

SEOUL = ZoneInfo("Asia/Seoul")

EXPERIENCE_MONTHS = {"ANY": 0, "MONTHS_3": 3, "MONTHS_6": 6, "YEAR_1": 12}
EXPERIENCE_CODES = {months: code for code, months in EXPERIENCE_MONTHS.items()}

# Applications still waiting for a selection. Once the shift has started nobody can be
# requested or accept any more, so they read as NOT_SELECTED (see `unselected_at_start`).
OPEN_APPLICATION_STATUSES = ("APPLIED", "REQUESTED")


def now() -> datetime:
    return utcnow()


def iso(value: datetime | None) -> str | None:
    return iso_utc(value)


def hhmm(value: time) -> str:
    return value.strftime("%H:%M")


def seoul_today(at: datetime) -> date:
    return at.astimezone(SEOUL).date()


@dataclass(frozen=True)
class ShiftTimes:
    start_at: datetime
    end_at: datetime

    @property
    def minutes(self) -> int:
        return int((self.end_at - self.start_at).total_seconds() // 60)


def shift_times(work_date: date, start: time, end: time, ends_next_day: bool) -> ShiftTimes:
    """UTC start/end of a Seoul shift; raises OverflowError past the calendar's end."""
    end_date = work_date + timedelta(days=1) if ends_next_day else work_date
    start_at = datetime.combine(work_date, start, SEOUL).astimezone(UTC)
    end_at = datetime.combine(end_date, end, SEOUL).astimezone(UTC)
    return ShiftTimes(start_at, end_at)


def job_times(job: JobPosting) -> ShiftTimes:
    return shift_times(job.work_date, job.start_time, job.end_time, job.ends_next_day)


def _seoul_clock(at: datetime) -> tuple[date, time]:
    """Seoul date and wall clock truncated to whole seconds.

    Stored times have whole minutes, so truncation keeps every comparison exact; MySQL would
    instead round a fractional parameter to the TIME(0) column (09:59:59.6 -> 10:00:00).
    """
    local = at.astimezone(SEOUL)
    return local.date(), local.time().replace(tzinfo=None, microsecond=0)


def starts_after(at: datetime):
    """SQL for startAt > at. Seoul has no DST, so the date and wall clock compare exactly."""
    today, clock = _seoul_clock(at)
    return or_(JobPosting.work_date > today,
               and_(JobPosting.work_date == today, JobPosting.start_time > clock))


def ends_after(at: datetime):
    """SQL for endAt > at; an overnight shift ends on the day after its work date."""
    today, clock = _seoul_clock(at)
    same_day = or_(JobPosting.work_date > today,
                   and_(JobPosting.work_date == today, JobPosting.end_time > clock))
    next_day = or_(JobPosting.work_date >= today,
                   and_(JobPosting.work_date == today - timedelta(days=1), JobPosting.end_time > clock))
    return or_(and_(JobPosting.ends_next_day.is_(False), same_day),
               and_(JobPosting.ends_next_day.is_(True), next_day))


def estimated_pay(hourly: int, minutes: int) -> int:
    """Hourly wage times scheduled minutes, floored to whole won (no breaks or premiums)."""
    return hourly * minutes // 60


def owned_store(db: Session, owner_id: str, store_id: str, *, lock: bool = False) -> Store:
    """The caller's APPROVED store: 404 RESOURCE_NOT_FOUND for a missing or foreign store
    (existence hidden), 403 STORE_APPROVAL_REQUIRED while PENDING."""
    return load_owned_store(db, owner_id, store_id, lock=lock, not_found=ErrorCode.RESOURCE_NOT_FOUND)


def unselected_at_start(status: str, job: JobPosting, at: datetime) -> bool:
    """An APPLIED/REQUESTED application of a posting whose shift has started reads NOT_SELECTED.

    The posting ended without choosing it, exactly like a manual closure (which stores
    NOT_SELECTED): applying, requesting and accepting all require a future start. Like
    COMPLETED this is a read-time projection; nothing is written.
    """
    return status in OPEN_APPLICATION_STATUSES and job_times(job).start_at <= at


def applicant_counts(db: Session, job_ids: Iterable[str], at: datetime) -> dict[str, int]:
    """JobPosting.applicantCount: applications other than WITHDRAWN/NOT_SELECTED as read at
    `at`, so open ones stop counting once the shift has started. `at` is the response's single
    clock read (its asOf), never a second one."""
    ids = list(job_ids)
    if not ids:
        return {}
    rows = db.execute(
        select(JobApplication.job_id, func.count())
        .join(JobPosting, JobPosting.id == JobApplication.job_id)
        .where(JobApplication.job_id.in_(ids),
               or_(JobApplication.status.in_(("CONFIRMED", "COMPLETED")),
                   and_(JobApplication.status.in_(OPEN_APPLICATION_STATUSES), starts_after(at))))
        .group_by(JobApplication.job_id)
    )
    return {job_id: count for job_id, count in rows}


def job_fields(job: JobPosting) -> dict:
    """The JobPostingCreate part of a posting, as submitted."""
    return {
        "title": job.title,
        "description": job.duty_description,
        "workPart": job.work_part,
        "workDate": job.work_date.isoformat(),
        "startTime": hhmm(job.start_time),
        "endTime": hhmm(job.end_time),
        "endsNextDay": job.ends_next_day,
        "minimumExperience": EXPERIENCE_CODES[job.min_experience_months],
        "experienceNotes": job.extra_requirements or "",
        "hourlyPay": job.hourly_wage_krw,
        "paymentTiming": job.payment_timing,
        "paymentNotes": job.pay_note or "",
    }


def job_body(job: JobPosting, store: Store, applicant_count: int) -> dict:
    """The `JobPosting` schema."""
    times = job_times(job)
    return {
        **job_fields(job),
        "id": job.id,
        "store": store_card(store),
        "status": job.status,
        "recruitmentCount": job.headcount,
        "applicantCount": applicant_count,
        "startAt": iso(times.start_at),
        "endAt": iso(times.end_at),
        "estimatedPay": estimated_pay(job.hourly_wage_krw, times.minutes),
        "createdAt": iso(job.created_at),
        "closedAt": iso(job.closed_at),
        "revision": job.revision,
    }
